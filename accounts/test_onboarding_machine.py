"""The onboarding machine, checked mechanically: no database, no HTTP.

The guards depend on four facts (a placeholder account with data, the AI
extension, whether the visitor is signed in and named, remote work or not).
The tests enumerate every combination of them — the sixteen "worlds" — and
prove reachability, totality and termination in each, rather than sampling
the two or three a walk-through would cover.
"""

from __future__ import annotations

from itertools import product
from typing import Any

from django.test import SimpleTestCase

from accounts.models import SalaryPeriod, weekly_cost
from accounts.onboarding import data
from accounts.onboarding.flow import EDITABLE, STEPS, machine
from accounts.onboarding.machine import (
    VERSION,
    Context,
    Event,
    IllegalTransition,
    Kind,
    Machine,
    Run,
    Step,
)
from accounts.onboarding.testing import DEFAULT_ANSWERS
from accounts.services import search_profile_fields

ALL_IDS = tuple(step.id for step in STEPS)


def world(*, waiting: bool = False, plugin: bool = False, named: bool = False, remote: bool = False):
    """A context and the answers that flip the four guards."""
    ctx = Context(
        authenticated=named,
        display_name_known=named,
        ai_plugin=plugin,
        waiting_applications=2 if waiting else 0,
    )
    answers = {"work_mode": "remote" if remote else "hybrid"}
    return ctx, answers


WORLDS = [
    world(waiting=w, plugin=p, named=n, remote=r) for w, p, n, r in product([False, True], repeat=4)
]


def walk(ctx: Context, answers: dict[str, Any] | None = None, *, until: str | None = None) -> Run:
    """Apply CONTINUE/SKIP from ``fresh()`` until the terminal (or ``until``)."""
    run = machine.fresh()
    overrides = answers or {}
    while True:
        state = machine.current(run, ctx)
        if state == until:
            return run
        step = machine.by_id[state]
        answer = {**DEFAULT_ANSWERS.get(state, {})}
        for key in step.answer_keys:
            if key in overrides:
                answer[key] = overrides[key]
        run = machine.apply(run, Event.CONTINUE, ctx, at=state, answer=answer)
        if run.state == machine.terminal:
            return run


class MachineStructureTests(SimpleTestCase):
    def test_check_passes_on_the_shipped_table(self):
        machine.check()

    def test_check_rejects_broken_tables(self):
        def build(steps, **kwargs):
            defaults = {"initial": "a", "review": "r", "terminal": "z"}
            return Machine(steps, **{**defaults, **kwargs})

        a = Step("a", "a", Kind.SINGLE, "r", answer_keys=("x",))
        r = Step("r", "r", Kind.REVIEW, "z")
        z = Step("z", "", Kind.TERMINAL, None, counted=False)
        build([a, r, z])  # the smallest valid table

        cases = {
            "duplicate id": [a, Step("a", "b", Kind.SINGLE, "r"), r, z],
            "duplicate slug": [a, Step("b", "a", Kind.SINGLE, "r"), r, z],
            "dangling next": [Step("a", "a", Kind.SINGLE, "nowhere"), r, z],
            "cycle": [Step("a", "a", Kind.SINGLE, "r"), Step("r", "r", Kind.REVIEW, "a"), z],
            "two terminals": [a, r, z, Step("y", "y", Kind.TERMINAL, None, counted=False)],
            "guarded terminal": [a, r, Step("z", "", Kind.TERMINAL, None, counted=False, guard=lambda a_, c: True)],
            "overlapping keys": [a, Step("b", "b", Kind.SINGLE, "r", answer_keys=("x",)), r, z],
            "skip values mismatch": [Step("a", "a", Kind.GRID, "r", answer_keys=("x",), skip_values={"y": 1}), r, z],
            "auto-advance on a multi": [Step("a", "a", Kind.MULTI, "r", auto_advance=True), r, z],
            "review after terminal": [Step("a", "a", Kind.SINGLE, "z"), z, r],
        }
        for name, steps in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                if name == "cycle":
                    build(steps)
                elif name == "review after terminal":
                    Machine([Step("a", "a", Kind.SINGLE, "z"), Step("z", "", Kind.TERMINAL, None, counted=False), r], initial="a", review="r", terminal="z")
                else:
                    build(steps)

    def test_every_step_is_reachable_in_some_world(self):
        reached: set[str] = set()
        for ctx, answers in WORLDS:
            reached.update(machine.path(answers, ctx))
        self.assertEqual(reached, set(ALL_IDS))

    def test_path_is_total_and_acyclic_in_every_world(self):
        unguarded = {step.id for step in STEPS if step.guard is None}
        for ctx, answers in WORLDS:
            with self.subTest(ctx=ctx, answers=answers):
                path = machine.path(answers, ctx)
                self.assertEqual(len(path), len(set(path)))
                self.assertEqual(path[-1], "done")
                self.assertTrue(unguarded <= set(path))
                self.assertEqual(
                    list(path), [s for s in ALL_IDS if s in path], "the path keeps the chain order"
                )

    def test_continue_walks_the_projected_path(self):
        for ctx, answers in WORLDS:
            with self.subTest(ctx=ctx, answers=answers):
                path = machine.path(answers, ctx)
                run = machine.fresh()
                events = 0
                while run.state != machine.terminal:
                    state = machine.current(run, ctx)
                    answer = {**DEFAULT_ANSWERS.get(state, {})}
                    if state == "work_mode":
                        answer = {"work_mode": answers["work_mode"]}
                    run = machine.apply(run, Event.CONTINUE, ctx, at=state, answer=answer)
                    events += 1
                self.assertEqual(events, len(path) - 1)
                self.assertEqual(run.visited, set(path) - {"done"})


class CounterTests(SimpleTestCase):
    def test_counter_extremes(self):
        ctx, answers = world(plugin=True)
        self.assertEqual(machine.position("status", answers, ctx), (1, 20))
        self.assertEqual(machine.position("plan", answers, ctx), (20, 20))
        ctx, answers = world(named=True, remote=True)
        self.assertEqual(machine.position("status", answers, ctx), (1, 17))
        self.assertEqual(machine.position("plan", answers, ctx), (17, 17))
        ctx, answers = world()
        self.assertEqual(machine.position("status", answers, ctx), (1, 19))
        ctx, answers = world(waiting=True)
        self.assertEqual(machine.position("welcome_back", answers, ctx), (0, 19))
        self.assertEqual(machine.position("intro_pace", answers, ctx), (2, 19))

    def test_counter_never_grows_during_a_run(self):
        ctx, _ = world()
        run = machine.fresh()
        previous = machine.position(machine.current(run, ctx), run.answers, ctx)[1]
        while run.state != machine.terminal:
            state = machine.current(run, ctx)
            answer = {**DEFAULT_ANSWERS.get(state, {})}
            if state == "work_mode":
                answer = {"work_mode": "remote"}
            run = machine.apply(run, Event.CONTINUE, ctx, at=state, answer=answer)
            if run.state == machine.terminal:
                break
            total = machine.position(run.state, run.answers, ctx)[1]
            self.assertLessEqual(total, previous)
            previous = total
        self.assertEqual(previous, 18)  # hybrid (19) dropped the cities


class ProjectionTests(SimpleTestCase):
    def test_current_skips_a_state_whose_guard_turned_false(self):
        ctx, _ = world()
        run = Run(state="identity", visited=frozenset({"review"}))
        named = Context(authenticated=True, display_name_known=True)
        self.assertEqual(machine.current(run, named), "cv")
        run = Run(state="intro_copilot")
        self.assertEqual(machine.current(run, ctx), "challenge")
        run = Run(state="cities", answers={"work_mode": "remote"})
        self.assertEqual(machine.current(run, ctx), "salary")

    def test_reachable_is_current_or_visited_on_path(self):
        ctx, _ = world()
        run = walk(ctx, until="salary")
        self.assertTrue(machine.reachable(run, "salary", ctx))
        self.assertTrue(machine.reachable(run, "cities", ctx))
        self.assertFalse(machine.reachable(run, "timeline", ctx))
        self.assertFalse(machine.reachable(run, "done", ctx))
        switched = Run(state="salary", visited=run.visited, answers={**run.answers, "work_mode": "remote"})
        self.assertFalse(machine.reachable(switched, "cities", ctx))

    def test_pred_returns_the_previously_visited_enabled_step(self):
        ctx, _ = world()
        run = walk(ctx, until="salary")
        self.assertEqual(machine.pred(run, "salary", ctx), "cities")
        switched = Run(state="salary", visited=run.visited, answers={**run.answers, "work_mode": "remote"})
        self.assertEqual(machine.pred(switched, "salary", ctx), "work_mode")
        self.assertIsNone(machine.pred(machine.fresh(), "status", ctx))

    def test_back_target_during_a_detour_is_the_review(self):
        ctx, _ = world()
        run = walk(ctx, until="review")
        detour = machine.apply(run, Event.EDIT, ctx, at="review", target="salary")
        self.assertEqual(machine.back_target(detour, "salary", ctx), "review")
        self.assertEqual(machine.back_target(detour, "review", ctx), "timeline")

    def test_identity_leaves_the_path_once_named(self):
        named = Context(authenticated=True, display_name_known=True)
        run = walk(named, until="cv")
        self.assertEqual(machine.pred(run, "cv", named), "review")

    def test_run_json_round_trip_and_corrupt_payloads(self):
        run = Run(state="salary", visited=frozenset({"status", "cities"}), answers={"work_mode": "hybrid"}, return_to="review")
        self.assertEqual(Run.from_json(run.to_json()), run)
        for raw in (None, [], {"v": VERSION - 1, "state": "salary", "visited": [], "answers": {}, "return_to": None},
                    {"v": VERSION, "state": "", "visited": [], "answers": {}, "return_to": None},
                    {"v": VERSION, "state": "salary", "visited": [1], "answers": {}, "return_to": None},
                    {"v": VERSION, "state": "salary", "visited": [], "answers": [], "return_to": None},
                    {"v": VERSION, "state": "salary", "visited": [], "answers": {}, "return_to": 3}):
            with self.subTest(raw=raw):
                self.assertIsNone(Run.from_json(raw))


class TransitionTests(SimpleTestCase):
    def test_edit_from_review_returns_to_review(self):
        ctx, _ = world()
        run = walk(ctx, until="review")
        detour = machine.apply(run, Event.EDIT, ctx, at="review", target="titles")
        self.assertEqual((detour.state, detour.return_to), ("titles", "review"))
        back = machine.apply(detour, Event.CONTINUE, ctx, at="titles", answer={"job_titles": ["Comptable"]})
        self.assertEqual((back.state, back.return_to), ("review", None))
        self.assertEqual(back.answers["job_titles"], ["Comptable"])
        # The review was seen and left by the edit; nothing else changed.
        self.assertEqual(back.visited, run.visited | {"review"})

    def test_edit_that_enables_a_step_continues_through_it_then_returns(self):
        ctx, _ = world(remote=True)
        run = walk(ctx, {"work_mode": "remote"}, until="review")
        self.assertNotIn("cities", run.visited)
        detour = machine.apply(run, Event.EDIT, ctx, at="review", target="work_mode")
        step = machine.apply(detour, Event.CONTINUE, ctx, at="work_mode", answer={"work_mode": "hybrid"})
        self.assertEqual((step.state, step.return_to), ("cities", "review"))
        back = machine.apply(step, Event.CONTINUE, ctx, at="cities", answer={"cities": ["Mons"], "search_radius_km": 20})
        self.assertEqual((back.state, back.return_to), ("review", None))

    def test_edit_return_passes_over_skipped_steps(self):
        ctx, _ = world()
        run = machine.fresh()
        while machine.current(run, ctx) != "review":
            state = machine.current(run, ctx)
            if state in ("industries", "salary"):
                run = machine.apply(run, Event.SKIP, ctx, at=state)
            else:
                run = machine.apply(run, Event.CONTINUE, ctx, at=state, answer=DEFAULT_ANSWERS.get(state, {}))
        detour = machine.apply(run, Event.EDIT, ctx, at="review", target="titles")
        back = machine.apply(detour, Event.CONTINUE, ctx, at="titles", answer={"job_titles": ["Comptable"]})
        self.assertEqual(back.state, "review")

    def test_review_sends_back_to_a_step_an_abandoned_detour_left_unanswered(self):
        ctx, _ = world(remote=True)
        run = walk(ctx, {"work_mode": "remote"}, until="review")
        detour = machine.apply(run, Event.EDIT, ctx, at="review", target="work_mode")
        parked = machine.apply(detour, Event.CONTINUE, ctx, at="work_mode", answer={"work_mode": "hybrid"})
        self.assertEqual(parked.state, "cities")
        # The visitor used the back arrow (the review) and pressed Continue there.
        again = machine.apply(parked, Event.CONTINUE, ctx, at="review")
        self.assertEqual((again.state, again.return_to), ("cities", "review"))
        back = machine.apply(again, Event.CONTINUE, ctx, at="cities", answer={"cities": ["Mons"], "search_radius_km": 20})
        self.assertEqual((back.state, back.return_to), ("review", None))

    def test_rewind_to_the_gate_keeps_the_answers_and_drops_later_visits(self):
        named = Context(authenticated=True, display_name_known=True)
        run = walk(named, until="plan")
        rewound = machine.rewind_to(run, "identity")
        self.assertEqual(rewound.state, "identity")
        self.assertEqual(rewound.answers, run.answers)
        self.assertNotIn("cv", rewound.visited)
        self.assertIn("review", rewound.visited)
        anonymous, _ = world()
        self.assertEqual(machine.current(rewound, anonymous), "identity")
        self.assertFalse(machine.reachable(rewound, "plan", anonymous))

    def test_continue_from_or_after_the_review_clears_a_stale_detour(self):
        ctx, _ = world()
        run = walk(ctx, until="review")
        stale = Run(state="review", visited=run.visited, answers=run.answers, return_to="review")
        after = machine.apply(stale, Event.CONTINUE, ctx, at="review")
        self.assertEqual((after.state, after.return_to), ("identity", None))

    def test_edit_is_refused_outside_the_review_and_for_bad_targets(self):
        ctx, _ = world()
        run = walk(ctx, until="review")
        for at, target in (("salary", "titles"), ("review", "cv"), ("review", "review"), ("review", "plan"), ("review", None)):
            with self.subTest(at=at, target=target), self.assertRaises(IllegalTransition):
                machine.apply(run, Event.EDIT, ctx, at=at, target=target)
        early = walk(ctx, until="salary")
        with self.assertRaises(IllegalTransition):
            machine.apply(early, Event.EDIT, ctx, at="review", target="titles")

    def test_edit_is_allowed_from_a_visited_review_during_a_detour(self):
        ctx, _ = world()
        run = walk(ctx, until="review")
        detour = machine.apply(run, Event.EDIT, ctx, at="review", target="titles")
        # The visitor pressed Back to the review and picked another pencil.
        switched = machine.apply(detour, Event.EDIT, ctx, at="review", target="salary")
        self.assertEqual((switched.state, switched.return_to), ("salary", "review"))

    def test_continue_at_a_visited_state_is_accepted_and_moves_forward(self):
        ctx, _ = world()
        run = walk(ctx, until="salary")
        again = machine.apply(run, Event.CONTINUE, ctx, at="challenge", answer={"challenge": "lost"})
        self.assertEqual(again.state, "intro_profile")
        self.assertEqual(again.answers["challenge"], "lost")
        with self.assertRaises(IllegalTransition):
            machine.apply(run, Event.CONTINUE, ctx, at="timeline", answer={"start_timeline": "open"})

    def test_skip_only_where_declared_and_writes_the_skip_values(self):
        ctx, _ = world()
        for state in ("industries", "cities", "salary", "cv"):
            with self.subTest(state=state):
                run = walk(ctx, until=state) if state != "cv" else Run(
                    state="cv", visited=frozenset(walk(ctx, until="identity").visited | {"identity"}),
                    answers={**walk(ctx, until="identity").answers, "display_name": "Lionel"},
                )
                ctx_for = ctx if state != "cv" else Context(authenticated=True, display_name_known=True)
                skipped = machine.apply(run, Event.SKIP, ctx_for, at=state)
                self.assertTrue(machine.answered(state, skipped.answers))
                self.assertNotEqual(skipped.state, state)
        with self.assertRaises(IllegalTransition):
            machine.apply(machine.fresh(), Event.SKIP, ctx, at="status")

    def test_continue_refuses_foreign_keys_and_incomplete_answers(self):
        ctx, _ = world()
        run = walk(ctx, until="salary")
        with self.assertRaises(IllegalTransition):
            machine.apply(run, Event.CONTINUE, ctx, at="salary", answer={"start_timeline": "asap"})
        with self.assertRaises(IllegalTransition):
            machine.apply(run, Event.CONTINUE, ctx, at="salary", answer={"salary_min": 3000})
        named = Context(authenticated=True, display_name_known=True)
        at_cv = walk(named, until="cv")
        with self.assertRaises(IllegalTransition):
            machine.apply(at_cv, Event.CONTINUE, named, at="cv")

    def test_stay_writes_without_moving(self):
        named = Context(authenticated=True, display_name_known=True)
        run = walk(named, until="cv")
        uploaded = machine.apply(
            run, Event.STAY, named, at="cv",
            answer={"cv_document_id": 7, "cv_text_chars": 900, "cv_redactions": "1 e-mail", "cv_analyzed": False, "cv_language": "fr"},
        )
        self.assertEqual(uploaded.state, "cv")
        self.assertIn("cv", uploaded.visited)
        after = machine.apply(uploaded, Event.CONTINUE, named, at="cv")
        self.assertEqual(after.state, "plan")

    def test_terminal_accepts_no_event_and_is_returned_only_by_continue_from_plan(self):
        named = Context(authenticated=True, display_name_known=True)
        run = walk(named, until="plan")
        done = machine.apply(run, Event.CONTINUE, named, at="plan", answer=DEFAULT_ANSWERS["plan"])
        self.assertEqual(done.state, "done")
        for event in Event:
            with self.subTest(event=event), self.assertRaises(IllegalTransition):
                machine.apply(done, event, named, at="done", target="titles")

    def test_plan_cannot_be_reached_while_the_gate_is_still_on_the_path(self):
        anonymous, _ = world()
        run = walk(anonymous, until="identity")
        self.assertEqual(machine.current(run, anonymous), "identity")
        with self.assertRaises(IllegalTransition):
            machine.apply(run, Event.CONTINUE, anonymous, at="plan")


class DataAndMappingTests(SimpleTestCase):
    def test_related_titles_are_known_titles(self):
        titles = set(data.JOB_TITLES)
        for key, related in data.RECOMMENDATIONS.items():
            self.assertIn(key, titles)
            for title in related:
                self.assertIn(title, titles)
                self.assertNotEqual(title, key)
        self.assertEqual(data.related_titles(["Un intitulé inconnu"]), [])
        picked = ["Software Engineer"]
        for title in data.related_titles(picked):
            self.assertNotIn(title, picked)
        self.assertLessEqual(len(data.related_titles(list(data.JOB_TITLES[:5]))), 8)
        self.assertEqual(len(data.INDUSTRIES_PRIMARY) + len(data.INDUSTRIES_MORE), 18)

    def test_editable_steps_precede_the_review(self):
        for step_id in EDITABLE:
            self.assertLess(ALL_IDS.index(step_id), ALL_IDS.index("review"))

    def test_search_profile_fields_mapping(self):
        fields = search_profile_fields({
            **{k: v for answer in DEFAULT_ANSWERS.values() for k, v in answer.items()},
            "help_wanted": ["track", "everything", "bogus"],
            "work_types": ["permanent", "any"],
            "industries": ["it", "unknown"],
        })
        self.assertEqual(fields["help_wanted"], ["track"])
        self.assertEqual(fields["work_types"], ["any"])
        self.assertEqual(fields["industries"], ["it"])
        self.assertEqual(fields["cities"], ["Nivelles", "Wavre"])
        self.assertEqual((fields["salary_min"], fields["salary_period"]), (5900, "month"))
        remote = search_profile_fields({"work_mode": "remote", "cities": ["Mons"], "any_industry": True, "industries": ["it"]})
        self.assertEqual((remote["cities"], remote["industries"], remote["any_industry"]), ([], [], True))
        empty = search_profile_fields({})
        self.assertEqual(empty["work_mode"], "unknown")
        self.assertEqual(empty["salary_period"], "")
        self.assertIsNone(empty["salary_min"])
        no_period = search_profile_fields({"salary_min": 3000, "salary_period": "week"})
        self.assertEqual((no_period["salary_min"], no_period["salary_period"]), (None, ""))
        bounded = search_profile_fields({"job_titles": [f"Poste {i}" for i in range(20)]})
        self.assertEqual(len(bounded["job_titles"]), 10)

    def test_weekly_cost(self):
        self.assertEqual(weekly_cost(5900, SalaryPeriod.MONTH), 1362)
        self.assertEqual(weekly_cost(25, SalaryPeriod.HOUR), 950)
        self.assertEqual(weekly_cost(60_000, SalaryPeriod.YEAR), 1154)
        self.assertIsNone(weekly_cost(None, SalaryPeriod.MONTH))
        self.assertIsNone(weekly_cost(3000, ""))
