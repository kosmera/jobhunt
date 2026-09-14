"""Independent chains, durable handoff, late results and real Q2 continuations."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from multiprocessing import Queue, Value
from threading import Barrier
from unittest import mock

import rls
from django.db import DatabaseError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from django_q.exceptions import TimeoutException
from django_q.models import OrmQ
from django_q.signing import SignedPackage

from jobhunt_ai.tests.fakes import make_premium_user as make_user
from rls.testing import app_role
from jobhunt_ai import conf
from jobhunt_ai.models import AgentRun, OfferLead, RunKind, RunStatus, ScoutTarget, TargetStatus
from jobhunt_ai.services import fanout, runner


class FanoutFixtures:
    def set_up_fanout(self):
        self.owner = make_user()
        self.agent_run = AgentRun.objects.create(owner=self.owner, kind=RunKind.SCOUT, status=RunStatus.RUNNING)
        self.context = {
            "run_id": self.agent_run.pk, "owner_id": self.owner.pk, "location": "Bruxelles",
            "radius_km": 40, "queries": ["Django"], "rubric": {"core_skills": ["Python"]},
        }
        self.sources = [
            {"name": "fast", "url": "https://fast.example/jobs", "query": "Django"},
            {"name": "slow", "url": "https://slow.example/jobs", "query": "Django"},
        ]
        self.lead = {"title": "Engineer", "company_name": "Acme", "url": "https://example.org/1", "score": 81}

    def dispatch(self):
        fanout.dispatch(self.agent_run.pk, self.owner.pk, self.context, self.sources)
        return list(self.agent_run.targets.order_by("source__name"))

    def scrape(self, target):
        with mock.patch("jobhunt_ai.scraping.sources.fetch_page", return_value="A job posting"):
            fanout.scrape_target(str(target.pk), self.owner.pk)

    def analyze(self, target):
        with mock.patch("jobhunt_ai.agents.scout.analyze_page", return_value=[self.lead]):
            fanout.analyze_target(str(target.pk), self.owner.pk)


class FanoutTests(FanoutFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.set_up_fanout()

    def test_revoked_premium_stops_scraping_and_already_queued_analysis(self):
        from accounts.models import Profile, SubscriptionLevel

        first, second = self.dispatch()
        self.scrape(first)
        Profile.objects.filter(user=self.owner).update(subscription_level=SubscriptionLevel.FREE)
        with mock.patch("jobhunt_ai.scraping.sources.fetch_page") as scrape:
            with mock.patch("jobhunt_ai.agents.scout.analyze_page") as analyze:
                fanout.scrape_target(str(second.pk), self.owner.pk)
                fanout.analyze_target(str(first.pk), self.owner.pk)
                scrape.assert_not_called()
                analyze.assert_not_called()
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.FAILED)
        self.assertFalse(OfferLead.objects.exists())
        for target in self.agent_run.targets.all():
            self.assertEqual(target.status, TargetStatus.FAILED)
            self.assertIn("Premium", target.error)

    def test_malformed_source_is_isolated_during_preparation(self):
        from jobhunt_ai.agents import scout
        from jobhunt_ai.tests.fakes import fake_llm
        from jobhunt_ai.tests.test_agents import make_profile

        self.agent_run.profile = make_profile(owner=self.owner)
        self.agent_run.params = {"keywords": "Django"}
        sources = [
            {"name": "broken", "url": "https://example.org/{missing}"},
            {"name": "invalid-type", "url": 123},
            {"name": "valid", "url": "https://example.org/{query}"},
        ]
        with fake_llm(), mock.patch("jobhunt_ai.conf.scout_sources", return_value=sources):
            context, prepared = scout.prepare(self.agent_run)
        self.assertEqual(len(prepared), 3)
        self.assertTrue(prepared[0]["configuration_error"])
        self.assertTrue(prepared[1]["configuration_error"])
        self.assertEqual(prepared[2]["url"], "https://example.org/Django")
        fanout.dispatch(self.agent_run.pk, self.owner.pk, context, prepared)
        broken, invalid, valid = list(self.agent_run.targets.order_by("source__name"))
        with self.assertRaises(RuntimeError):
            fanout.scrape_target(str(broken.pk), self.owner.pk)
        with self.assertRaises(RuntimeError):
            fanout.scrape_target(str(invalid.pk), self.owner.pk)
        self.scrape(valid)
        self.analyze(valid)
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.SUCCEEDED)
        self.assertEqual(self.agent_run.result["targets_failed"], 2)

    def test_dispatch_has_no_network_calls_and_one_chain_per_target(self):
        with mock.patch("jobhunt_ai.scraping.sources.fetch_page") as fetch:
            targets = self.dispatch()
        fetch.assert_not_called()
        self.assertEqual(len(targets), 2)
        self.assertEqual(OrmQ.objects.count(), 2)
        for row in OrmQ.objects.all():
            package = SignedPackage.loads(row.payload)
            self.assertEqual(package["func"], "jobhunt_ai.services.fanout.scrape_target")
            self.assertEqual(package["timeout"], conf.SCOUT_SCRAPE_TIMEOUT)
            self.assertEqual(len(package["chain"]), 1)
            analysis, args, options = package["chain"][0]
            self.assertEqual(analysis, "jobhunt_ai.services.fanout.analyze_target")
            self.assertEqual(args, package["args"])
            self.assertEqual(options["timeout"], conf.SCOUT_ANALYZE_TIMEOUT)
            self.assertTrue(package["save"])
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.RUNNING)
        self.assertEqual(self.agent_run.progress_total, 2)
        self.assertIsNone(self.agent_run.deadline_at)

    def test_outer_rollback_removes_manifest_and_messages(self):
        with self.assertRaises(ValueError), transaction.atomic():
            self.dispatch()
            raise ValueError("rollback")
        self.assertFalse(ScoutTarget.objects.exists())
        self.assertFalse(OrmQ.objects.exists())
        self.agent_run.refresh_from_db()
        self.assertIsNone(self.agent_run.fanout_started_at)

    def test_dispatch_is_idempotent_and_deduplicates_targets(self):
        self.sources.append(self.sources[0])
        self.dispatch()
        self.dispatch()
        self.assertEqual(ScoutTarget.objects.count(), 2)
        self.assertEqual(OrmQ.objects.count(), 2)

    def test_one_target_can_finish_before_another_is_scraped(self):
        fast, slow = self.dispatch()
        self.scrape(fast)
        self.analyze(fast)
        fast.refresh_from_db()
        slow.refresh_from_db()
        self.agent_run.refresh_from_db()
        self.assertEqual(fast.status, TargetStatus.SUCCEEDED)
        self.assertEqual(slow.status, TargetStatus.PENDING)
        self.assertEqual(self.agent_run.status, RunStatus.RUNNING)
        self.assertEqual(self.agent_run.progress_current, 1)
        self.assertEqual(OfferLead.objects.count(), 1)

    def test_failed_scrape_does_not_run_analysis_or_cancel_sibling(self):
        fast, slow = self.dispatch()
        with mock.patch("jobhunt_ai.scraping.sources.fetch_page", side_effect=OSError("private-token")):
            with self.assertRaises(RuntimeError):
                fanout.scrape_target(str(slow.pk), self.owner.pk)
        # Q2 advances chains on failure. The next step must explicitly guard.
        with mock.patch("jobhunt_ai.agents.scout.analyze_page") as analyze:
            fanout.analyze_target(str(slow.pk), self.owner.pk)
        analyze.assert_not_called()
        self.scrape(fast)
        self.analyze(fast)
        self.agent_run.refresh_from_db()
        slow.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.SUCCEEDED)
        self.assertTrue(self.agent_run.result["partial"])
        self.assertEqual(self.agent_run.result["targets_failed"], 1)
        self.assertEqual(self.agent_run.result["created"], 1)
        self.assertNotIn("private-token", slow.error)

    def test_analysis_timeout_only_fails_its_target(self):
        fast, slow = self.dispatch()
        self.scrape(slow)
        with mock.patch("jobhunt_ai.agents.scout.analyze_page", side_effect=TimeoutException("timeout")):
            with self.assertRaises(TimeoutException):
                fanout.analyze_target(str(slow.pk), self.owner.pk)
        self.scrape(fast)
        self.analyze(fast)
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.SUCCEEDED)
        self.assertEqual(self.agent_run.result["targets_failed"], 1)

    def test_http_failure_is_visible_without_changing_sibling_execution(self):
        import httpx

        fast, slow = self.dispatch()
        response = httpx.Response(403, request=httpx.Request("GET", "https://source.example/jobs"))
        with mock.patch("jobhunt_ai.conf.BRIGHTDATA_API_TOKEN", ""), mock.patch(
            "httpx.get", return_value=response,
        ), self.assertRaisesRegex(RuntimeError, "ScoutTarget"):
            fanout.scrape_target(str(slow.pk), self.owner.pk)
        slow.refresh_from_db()
        self.assertIn("HTTP 403", slow.error)
        self.assertIn("Bright Data", slow.error)
        with mock.patch("jobhunt_ai.agents.scout.analyze_page") as analyze:
            fanout.analyze_target(str(slow.pk), self.owner.pk)
        analyze.assert_not_called()
        self.scrape(fast)
        self.analyze(fast)
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.SUCCEEDED)
        self.assertTrue(self.agent_run.result["partial"])

    def test_coordinator_failure_after_dispatch_cannot_cancel_children(self):
        self.dispatch()
        self.agent_run.mark_failed("coordinator timed out")
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.RUNNING)
        self.assertEqual(self.agent_run.targets.filter(status=TargetStatus.PENDING).count(), 2)

    def test_dead_worker_expires_independently_of_siblings(self):
        fast, slow = self.dispatch()
        ScoutTarget.objects.filter(pk=slow.pk).update(
            status=TargetStatus.SCRAPING, deadline_at=timezone.now() - timedelta(seconds=1),
        )
        # Even an obsolete coordinator deadline must not close the fan-out.
        AgentRun.objects.filter(pk=self.agent_run.pk).update(deadline_at=timezone.now() - timedelta(days=1))
        runner.sweep_orphans(self.owner)
        self.agent_run.refresh_from_db()
        slow.refresh_from_db()
        fast.refresh_from_db()
        self.assertEqual(slow.status, TargetStatus.FAILED)
        self.assertEqual(fast.status, TargetStatus.PENDING)
        self.assertEqual(self.agent_run.status, RunStatus.RUNNING)
        self.scrape(fast)
        self.analyze(fast)
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.SUCCEEDED)

    def test_lost_continuation_expires_without_stalling_parent_forever(self):
        fast, slow = self.dispatch()
        self.scrape(slow)
        ScoutTarget.objects.filter(pk=slow.pk).update(deadline_at=timezone.now() - timedelta(seconds=1))
        runner.sweep_orphans(self.owner)
        self.scrape(fast)
        self.analyze(fast)
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.SUCCEEDED)
        self.assertEqual(self.agent_run.result["targets_failed"], 1)

    def test_duplicate_deliveries_do_not_repeat_network_calls_or_writes(self):
        fast, _ = self.dispatch()
        with mock.patch("jobhunt_ai.scraping.sources.fetch_page", return_value="posting") as fetch:
            fanout.scrape_target(str(fast.pk), self.owner.pk)
            fanout.scrape_target(str(fast.pk), self.owner.pk)
        fetch.assert_called_once()
        with mock.patch("jobhunt_ai.agents.scout.analyze_page", return_value=[self.lead]) as analyze:
            fanout.analyze_target(str(fast.pk), self.owner.pk)
            fanout.analyze_target(str(fast.pk), self.owner.pk)
        analyze.assert_called_once()
        self.assertEqual(OfferLead.objects.count(), 1)

    def test_late_analysis_result_cannot_write_after_deadline_recovery(self):
        fast, _ = self.dispatch()
        self.scrape(fast)

        def late_result(*args):
            ScoutTarget.objects.filter(pk=fast.pk).update(deadline_at=timezone.now() - timedelta(seconds=1))
            fanout.reconcile_targets(self.owner.pk)
            return [self.lead]

        with mock.patch("jobhunt_ai.agents.scout.analyze_page", side_effect=late_result):
            fanout.analyze_target(str(fast.pk), self.owner.pk)
        self.assertFalse(OfferLead.objects.exists())
        fast.refresh_from_db()
        self.assertEqual(fast.status, TargetStatus.FAILED)

    def test_persistence_failure_rolls_back_only_one_target(self):
        fast, slow = self.dispatch()
        self.scrape(fast)
        self.scrape(slow)
        with mock.patch("jobhunt_ai.agents.scout.analyze_page", return_value=[self.lead]), mock.patch.object(
            OfferLead.objects, "bulk_create", side_effect=DatabaseError("offline"),
        ):
            with self.assertRaises(RuntimeError):
                fanout.analyze_target(str(slow.pk), self.owner.pk)
        self.analyze(fast)
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.SUCCEEDED)
        self.assertEqual(OfferLead.objects.count(), 1)

    def test_late_result_is_rejected_even_before_the_reconciler_runs(self):
        fast, _ = self.dispatch()
        self.scrape(fast)

        def late_result(*args):
            ScoutTarget.objects.filter(pk=fast.pk).update(deadline_at=timezone.now() - timedelta(seconds=1))
            return [self.lead]

        with mock.patch("jobhunt_ai.agents.scout.analyze_page", side_effect=late_result):
            fanout.analyze_target(str(fast.pk), self.owner.pk)
        self.assertFalse(OfferLead.objects.exists())
        fast.refresh_from_db()
        self.assertEqual(fast.status, TargetStatus.FAILED)

    def test_other_owner_cannot_claim_or_read_target(self):
        fast, _ = self.dispatch()
        other = make_user()
        with mock.patch("jobhunt_ai.scraping.sources.fetch_page") as fetch:
            fanout.scrape_target(str(fast.pk), other.pk)
        fetch.assert_not_called()
        with app_role(), rls.as_user(other):
            if connection.vendor == "postgresql":
                self.assertFalse(ScoutTarget.objects.filter(pk=fast.pk).exists())


class FanoutTransactionTests(FanoutFixtures, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.set_up_fanout()

    def process_message(self, row):
        from django_q.brokers import get_broker
        from django_q.monitor import monitor
        from django_q.worker import worker

        task = SignedPackage.loads(row.payload)
        task["ack_id"] = row.pk
        tasks, results = Queue(), Queue()
        for queue in (tasks, results):
            self.addCleanup(queue.join_thread)
            self.addCleanup(queue.close)
        tasks.put(task)
        tasks.put("STOP")
        worker(tasks, results, Value("f", -1), timeout=30)
        results.put("STOP")
        monitor(results, get_broker())

    def test_real_chain_pipes_committed_scrape_to_analysis_without_barrier(self):
        fast, slow = self.dispatch()
        first = next(row for row in OrmQ.objects.all() if SignedPackage.loads(row.payload)["args"][0] == str(fast.pk))

        def fetch(source):
            self.assertFalse(connection.in_atomic_block)
            return "Persisted handoff"

        with mock.patch("jobhunt_ai.scraping.sources.fetch_page", side_effect=fetch):
            self.process_message(first)
        analysis = next(
            row for row in OrmQ.objects.all()
            if SignedPackage.loads(row.payload)["func"] == "jobhunt_ai.services.fanout.analyze_target"
        )

        def analyze(source, text, context):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(text, "Persisted handoff")
            self.assertEqual(context["run_id"], self.agent_run.pk)
            return [self.lead]

        with mock.patch("jobhunt_ai.agents.scout.analyze_page", side_effect=analyze):
            self.process_message(analysis)
        fast.refresh_from_db()
        slow.refresh_from_db()
        self.assertEqual(fast.status, TargetStatus.SUCCEEDED)
        self.assertEqual(slow.status, TargetStatus.PENDING)

    @skipUnlessDBFeature("has_select_for_update")
    def test_parallel_analyses_deduplicate_at_commit(self):
        fast, slow = self.dispatch()
        self.scrape(fast)
        self.scrape(slow)
        barrier = Barrier(2)

        def analyze(*args):
            self.assertFalse(connection.in_atomic_block)
            barrier.wait(timeout=10)
            return [self.lead]

        def work(target):
            try:
                fanout.analyze_target(str(target.pk), self.owner.pk)
            finally:
                connections.close_all()

        with mock.patch("jobhunt_ai.agents.scout.analyze_page", side_effect=analyze):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(work, target) for target in (fast, slow)]
                for future in futures:
                    future.result(timeout=15)
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.SUCCEEDED)
        self.assertEqual(self.agent_run.result["targets_succeeded"], 2)
        self.assertEqual(self.agent_run.result["created"], 1)
        self.assertEqual(OfferLead.objects.count(), 1)
