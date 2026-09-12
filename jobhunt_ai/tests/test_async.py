"""Durability, tenant isolation and HTTP contracts, without network calls."""

import io
from multiprocessing import Queue, Value
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.db import DatabaseError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from django_q.models import OrmQ
from django_q.exceptions import TimeoutException
from django_q.signing import SignedPackage

from jobhunt_ai.tests.fakes import make_premium_user as make_user
from jobhunt_ai import checks
from jobhunt_ai.models import AgentRun, OfferLead, RunKind, RunStatus
from jobhunt_ai.services import runner
from jobhunt_ai.tests.fakes import PremiumTestCase
from jobhunt_ai.tests.test_agents import make_application, make_profile


class DurableSubmissionTests(TestCase):
    def setUp(self):
        self.owner = make_user()

    def test_submission_queues_only_ids_and_never_executes_in_request(self):
        with mock.patch.object(runner, "_dispatch") as dispatch:
            run = runner.launch(RunKind.SCOUT, owner=self.owner, params={"keywords": "private"})
        dispatch.assert_not_called()
        self.assertEqual(run.status, RunStatus.PENDING)
        package = SignedPackage.loads(OrmQ.objects.get().payload)
        self.assertEqual(package["args"], (run.pk, self.owner.pk))
        self.assertEqual(package["kwargs"], {})
        self.assertEqual(package["id"], run.task_id)
        self.assertFalse(package["sync"])

    def test_outer_rollback_removes_run_and_queue_message(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            runner.launch(RunKind.SCOUT, owner=self.owner)
            self.assertEqual(OrmQ.objects.count(), 1)
            raise RuntimeError("rollback")
        self.assertFalse(AgentRun.objects.exists())
        self.assertFalse(OrmQ.objects.exists())

    def test_broker_failure_rolls_back_run(self):
        with mock.patch("django_q.tasks.async_task", side_effect=DatabaseError("offline")):
            with self.assertRaises(DatabaseError):
                runner.launch(RunKind.SCOUT, owner=self.owner)
        self.assertFalse(AgentRun.objects.exists())

    def test_duplicate_scout_submission_reuses_active_run(self):
        first = runner.launch(RunKind.SCOUT, owner=self.owner)
        second = runner.launch(RunKind.SCOUT, owner=self.owner)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(OrmQ.objects.count(), 1)

    def test_application_match_and_generation_share_active_exclusion(self):
        application = make_application(owner=self.owner)
        first = runner.launch(RunKind.MATCH, owner=self.owner, application=application)
        second = runner.launch(RunKind.GENERATE_CV, owner=self.owner, application=application)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(OrmQ.objects.count(), 1)

    def test_rejects_related_object_from_another_owner(self):
        profile = make_profile(owner=make_user())
        with self.assertRaises(ValueError):
            runner.launch(RunKind.SCOUT, owner=self.owner, profile=profile)
        self.assertFalse(OrmQ.objects.exists())

    def test_profile_is_selected_at_submission(self):
        profile = make_profile(owner=self.owner)
        run = runner.launch(RunKind.SCOUT, owner=self.owner)
        self.assertEqual(run.profile_id, profile.pk)


class WorkerTests(TestCase):
    def setUp(self):
        self.owner = make_user()
        self.agent_run = AgentRun.objects.create(owner=self.owner, kind=RunKind.SCOUT)

    def test_duplicate_delivery_dispatches_once_and_preserves_success(self):
        with mock.patch.object(runner, "_dispatch", return_value={"created": 2}) as dispatch:
            runner.execute_queued(self.agent_run.pk, self.owner.pk)
            runner.execute_queued(self.agent_run.pk, self.owner.pk)
        dispatch.assert_called_once()
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.SUCCEEDED)
        self.assertEqual(self.agent_run.result, {"created": 2})
        self.assertIsNotNone(self.agent_run.started_at)
        self.assertIsNotNone(self.agent_run.finished_at)

    def test_wrong_owner_cannot_claim_even_without_postgres_rls(self):
        with mock.patch.object(runner, "_dispatch") as dispatch:
            runner.execute_queued(self.agent_run.pk, make_user().pk)
        dispatch.assert_not_called()
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.PENDING)

    def test_failure_sets_terminal_state_and_is_reported_to_queue(self):
        with mock.patch.object(runner, "_dispatch", side_effect=KeyError("private-token")):
            with self.assertRaisesRegex(RuntimeError, "AgentRun"):
                runner.execute_queued(self.agent_run.pk, self.owner.pk)
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.FAILED)
        self.assertNotIn("private-token", self.agent_run.error)
        with mock.patch.object(runner, "_dispatch") as dispatch:
            runner.execute_queued(self.agent_run.pk, self.owner.pk)
        dispatch.assert_not_called()

    def test_expired_worker_delivery_fails_without_repeating_llm(self):
        AgentRun.objects.filter(pk=self.agent_run.pk).update(
            status=RunStatus.RUNNING, deadline_at=timezone.now() - timedelta(seconds=1),
        )
        with mock.patch.object(runner, "_dispatch") as dispatch:
            runner.execute_queued(self.agent_run.pk, self.owner.pk)
        dispatch.assert_not_called()
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.FAILED)

    def test_q2_timeout_records_failure_and_preserves_worker_recycling(self):
        with mock.patch.object(runner, "_dispatch", side_effect=TimeoutException("timeout")):
            with self.assertRaises(TimeoutException):
                runner.execute_queued(self.agent_run.pk, self.owner.pk)
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.FAILED)
        self.assertIsNotNone(self.agent_run.finished_at)

    def test_live_worker_delivery_does_not_steal_claim(self):
        AgentRun.objects.filter(pk=self.agent_run.pk).update(
            status=RunStatus.RUNNING, deadline_at=timezone.now() + timedelta(minutes=5),
        )
        with mock.patch.object(runner, "_dispatch") as dispatch:
            runner.execute_queued(self.agent_run.pk, self.owner.pk)
        dispatch.assert_not_called()
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.RUNNING)

    def test_delayed_pending_message_does_not_start_after_expiry(self):
        AgentRun.objects.filter(pk=self.agent_run.pk).update(
            deadline_at=timezone.now() - timedelta(seconds=1),
        )
        with mock.patch.object(runner, "_dispatch") as dispatch:
            runner.execute_queued(self.agent_run.pk, self.owner.pk)
        dispatch.assert_not_called()
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.FAILED)

    def test_late_success_cannot_overwrite_terminal_failure(self):
        stale_copy = AgentRun.objects.get(pk=self.agent_run.pk)
        self.agent_run.mark_failed("expired")
        stale_copy.mark_succeeded({"created": 10})
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.FAILED)
        self.assertEqual(self.agent_run.result, {})

    def test_reconciler_works_without_polling(self):
        AgentRun.objects.filter(pk=self.agent_run.pk).update(
            deadline_at=timezone.now() - timedelta(seconds=1),
        )
        call_command("reconcile_ai_runs", stdout=io.StringIO())
        self.agent_run.refresh_from_db()
        self.assertEqual(self.agent_run.status, RunStatus.FAILED)


class WorkerTransactionTests(TransactionTestCase):
    def test_q2_broker_worker_monitor_roundtrip(self):
        from django_q.brokers import get_broker
        from django_q.brokers.orm import ORM
        from django_q.models import Task
        from django_q.monitor import monitor
        from django_q.worker import worker

        owner = make_user()
        run = runner.launch(RunKind.SCOUT, owner=owner)
        broker = get_broker()
        assert isinstance(broker, ORM), "Atomic submission requires the ORM broker."
        messages = broker.dequeue()
        assert messages, "The committed run must have a queue message."
        receipt, payload = messages[0]
        package = SignedPackage.loads(payload)
        package["ack_id"] = receipt
        tasks, results = Queue(), Queue()
        for queue in (tasks, results):
            self.addCleanup(queue.join_thread)
            self.addCleanup(queue.close)
        tasks.put(package)
        tasks.put("STOP")
        with mock.patch.object(runner, "_dispatch", return_value={"created": 3}):
            worker(tasks, results, Value("f", -1), timeout=10)
        results.put("STOP")
        monitor(results, broker)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.SUCCEEDED)
        self.assertEqual(run.result, {"created": 3})
        self.assertFalse(OrmQ.objects.exists())
        task = Task.objects.get(pk=run.task_id)
        self.assertTrue(task.success)
        self.assertIsNone(task.result)  # Private results only live in AgentRun.

    def test_network_work_has_no_database_transaction_open(self):
        owner = make_user()
        run = runner.launch(RunKind.SCOUT, owner=owner)

        def dispatch(claimed):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(claimed.status, RunStatus.RUNNING)
            return {"created": 0}

        with mock.patch.object(runner, "_dispatch", side_effect=dispatch):
            runner.execute_queued(run.pk, owner.pk)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_submissions_create_one_job_and_one_message(self):
        owner = make_user()
        barrier = Barrier(2)

        def submit():
            try:
                barrier.wait(timeout=10)
                return runner.launch(RunKind.SCOUT, owner=owner).pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit) for _ in range(2)]
            ids = [future.result(timeout=15) for future in futures]
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(OrmQ.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_deliveries_have_only_one_active_worker(self):
        owner = make_user()
        run = runner.launch(RunKind.SCOUT, owner=owner)
        started, finish = Event(), Event()

        def dispatch(claimed):
            started.set()
            self.assertTrue(finish.wait(timeout=10))
            return {"created": 0}

        def work():
            try:
                runner.execute_queued(run.pk, owner.pk)
            finally:
                connections.close_all()

        with mock.patch.object(runner, "_dispatch", side_effect=dispatch) as mocked:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(work)
                try:
                    self.assertTrue(started.wait(timeout=10))
                    pool.submit(work).result(timeout=5)
                finally:
                    finish.set()
                first.result(timeout=10)
        mocked.assert_called_once()


class AsyncAPITests(PremiumTestCase):
    payload = {"keywords": "Django", "location": "Bruxelles", "radius_km": 40}

    def start(self):
        return self.client.post(
            reverse("jobhunt_ai:api_scout_start"), self.payload, content_type="application/json",
        )

    def test_post_returns_202_location_and_pending_without_llm(self):
        make_profile(owner=self.user)
        with mock.patch.object(runner, "_dispatch") as dispatch:
            response = self.start()
        dispatch.assert_not_called()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "PENDING")
        self.assertEqual(response.headers["Location"], response.json()["status_url"])
        self.assertEqual(response.headers["Retry-After"], "3")
        self.assertEqual(OrmQ.objects.count(), 1)

    def test_invalid_parameters_create_no_job(self):
        response = self.client.post(
            reverse("jobhunt_ai:api_scout_start"), {**self.payload, "radius_km": 1000},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AgentRun.objects.exists())

    def test_missing_profile_returns_conflict(self):
        self.assertEqual(self.start().status_code, 409)

    def test_queue_failure_is_not_acknowledged_as_accepted(self):
        make_profile(owner=self.user)
        with mock.patch("django_q.tasks.async_task", side_effect=DatabaseError("secret")):
            response = self.start()
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, "secret", status_code=503)
        self.assertFalse(AgentRun.objects.exists())

    def test_status_and_results_are_scoped_to_owner(self):
        run = AgentRun.objects.create(owner=make_user(), kind=RunKind.SCOUT)
        for route in ("api_run_status", "api_run_leads", "run_status"):
            with self.subTest(route=route):
                response = self.client.get(reverse(f"jobhunt_ai:{route}", args=[run.pk]))
                self.assertEqual(response.status_code, 404)

    def test_poll_maps_status_and_never_exposes_params(self):
        run = AgentRun.objects.create(
            owner=self.user, kind=RunKind.SCOUT, status=RunStatus.RUNNING,
            params={"text": "private CV"}, phase="Lecture", progress_current=1, progress_total=8,
        )
        response = self.client.get(reverse("jobhunt_ai:api_run_status", args=[run.pk]))
        self.assertEqual(response.json()["status"], "PROCESSING")
        self.assertEqual(response.json()["progress"], {"current": 1, "total": 8})
        self.assertNotContains(response, "private CV")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_results_are_paginated_and_scoped_to_run(self):
        run = AgentRun.objects.create(owner=self.user, kind=RunKind.SCOUT)
        OfferLead.objects.bulk_create([
            OfferLead(owner=self.user, run=run, title=f"Offer {i}", company_name="Acme")
            for i in range(51)
        ])
        OfferLead.objects.create(owner=self.user, title="Another run", company_name="Other")
        response = self.client.get(reverse("jobhunt_ai:api_run_leads", args=[run.pk]))
        self.assertEqual(response.json()["count"], 51)
        self.assertEqual(len(response.json()["results"]), 50)
        self.assertEqual(response.json()["next_page"], 2)

    def test_get_cannot_submit_work(self):
        response = self.client.get(reverse("jobhunt_ai:api_scout_start"))
        self.assertEqual(response.status_code, 405)


class QueueConfigurationTests(TestCase):
    def test_unsafe_prefetch_retry_window_is_rejected(self):
        with override_settings(Q_CLUSTER={**settings.Q_CLUSTER, "retry": 1801}):
            self.assertIn("jobhunt_ai.E003", [error.id for error in checks.check_queue(None)])

    def test_external_broker_and_sync_are_rejected(self):
        with override_settings(Q_CLUSTER={**settings.Q_CLUSTER, "orm": None, "sync": True}):
            errors = [error.id for error in checks.check_queue(None)]
            self.assertIn("jobhunt_ai.E001", errors)
            self.assertIn("jobhunt_ai.E002", errors)
