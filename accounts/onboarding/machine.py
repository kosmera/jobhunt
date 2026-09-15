"""A finite state machine for a questionnaire: a chain with optional nodes.

Every screen is a state. The states form one forward chain (``Step.next``);
a state that only applies in some situations carries a *guard*, a pure
function of the answers so far and of a small :class:`Context`. Everything
else — the path a visitor will follow, the state they are effectively on,
where the back arrow goes, the ``n/N`` counter, the detour from the review
screen and back — is a projection of that one table plus the answers. There
is no second table to keep in sync, and :meth:`Machine.check` verifies the
table at import time.

Four events move a :class:`Run`: ``CONTINUE`` (answer, go forward),
``STAY`` (answer without moving — the CV upload shows its result on the
same screen), ``SKIP`` (write the step's skip values, go forward) and
``EDIT`` (from the review, jump back to a visited step and remember to come
back). A transition that is not allowed — a stale tab, a forged POST at a
state the visitor never reached — raises :class:`IllegalTransition` rather
than corrupting the run.

Nothing here knows about sessions, users or HTTP: the run is a frozen value,
the web layer stores it and builds the context.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

#: Bump when the answers' shape changes: an older run in a session is dropped.
VERSION = 1


class Kind(StrEnum):
    """How a screen is rendered; the machine only reads it for ``check``."""

    SINGLE = "single"
    MULTI = "multi"
    INTERSTITIAL = "interstitial"
    CHIPS = "chips"
    GRID = "grid"
    CITIES = "cities"
    SALARY = "salary"
    REVIEW = "review"
    GATE = "gate"
    FILE = "file"
    PLAN = "plan"
    TERMINAL = "terminal"


class Event(StrEnum):
    CONTINUE = "continue"  # answer (may be empty), then move forward
    STAY = "stay"  # answer without moving (the CV upload)
    SKIP = "skip"  # write the skip values, then move forward
    EDIT = "edit"  # from the review: jump to a visited step, remember to come back


@dataclass(frozen=True)
class Context:
    """What guards may read besides the answers. Built by the view, never by the machine."""

    authenticated: bool = False
    display_name_known: bool = False
    ai_plugin: bool = False
    waiting_applications: int = 0
    account_at_end: bool = False


Answers = Mapping[str, Any]
Guard = Callable[[Answers, Context], bool]


@dataclass(frozen=True)
class Step:
    id: str  # English, stable
    slug: str  # French URL segment ("" for the terminal)
    kind: Kind
    next: str | None  # None only for the terminal
    title: str = ""  # French h1 (interstitials and the plan keep their copy in the template)
    lede: str = ""
    note: str = ""  # small info line under the actions
    guard: Guard | None = None  # None: always on the path
    answer_keys: tuple[str, ...] = ()
    skip_values: Mapping[str, Any] | None = None  # None: SKIP refused
    counted: bool = True
    auto_advance: bool = False
    template: str = ""  # default: accounts/onboarding/<kind>.html

    @property
    def skippable(self) -> bool:
        return self.skip_values is not None


@dataclass(frozen=True)
class Run:
    """Where a visitor is, what they answered, where they have been.

    ``visited`` holds every state an event was applied at — the states the
    visitor actually saw and left — never the current one. It is a set and
    only grows: the back arrow, a browser Back and a typed URL all read it.
    ``return_to`` is set during an edit detour from the review.
    """

    state: str
    visited: frozenset[str] = frozenset()
    answers: Mapping[str, Any] = field(default_factory=dict)  # never mutated in place
    return_to: str | None = None
    version: int = VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "v": self.version,
            "state": self.state,
            "visited": sorted(self.visited),
            "answers": dict(self.answers),
            "return_to": self.return_to,
        }

    @classmethod
    def from_json(cls, raw: object) -> Run | None:
        """Shape validation only (the store checks the ids). Anything odd is ``None``."""
        if not isinstance(raw, dict) or raw.get("v") != VERSION:
            return None
        state = raw.get("state")
        visited = raw.get("visited")
        answers = raw.get("answers")
        return_to = raw.get("return_to")
        if not isinstance(state, str) or not state:
            return None
        if not isinstance(visited, list) or not all(isinstance(v, str) for v in visited):
            return None
        if not isinstance(answers, dict) or not all(isinstance(k, str) for k in answers):
            return None
        if return_to is not None and not isinstance(return_to, str):
            return None
        return cls(state=state, visited=frozenset(visited), answers=dict(answers), return_to=return_to)


class IllegalTransition(Exception):
    """The event is not allowed from this run in this context (stale tab, forged POST)."""


class Machine:
    def __init__(self, steps: Sequence[Step], *, initial: str, review: str, terminal: str) -> None:
        self.steps: tuple[Step, ...] = tuple(steps)
        # ``setdefault``: a duplicate must not silently win, ``check()`` reports it.
        self.by_id: dict[str, Step] = {}
        self.by_slug: dict[str, Step] = {}
        for step in self.steps:
            self.by_id.setdefault(step.id, step)
            if step.slug:
                self.by_slug.setdefault(step.slug, step)
        self.initial = initial
        self.review = review
        self.terminal = terminal
        self.check()

    # -- projections (pure) ------------------------------------------------

    def enabled(self, step_id: str, answers: Answers, ctx: Context) -> bool:
        guard = self.by_id[step_id].guard
        return guard is None or bool(guard(answers, ctx))

    def _chain(self) -> tuple[str, ...]:
        """Every step id from ``initial`` following ``next``, guards ignored."""
        ids: list[str] = []
        current: str | None = self.initial
        while current is not None:
            ids.append(current)
            current = self.by_id[current].next
        return tuple(ids)

    def path(self, answers: Answers, ctx: Context) -> tuple[str, ...]:
        """The enabled steps from ``initial`` in order; always ends with the terminal."""
        return tuple(s for s in self._chain() if self.enabled(s, answers, ctx))

    def current(self, run: Run, ctx: Context) -> str:
        """``run.state`` while its guard holds, else the next enabled step — never backwards."""
        state = run.state
        while not self.enabled(state, run.answers, ctx):
            following = self.by_id[state].next
            assert following is not None  # the terminal is unguarded: check()
            state = following
        return state

    def succ(self, state: str, answers: Answers, ctx: Context) -> str:
        """The next enabled step after ``state``; the terminal is always found."""
        following = self.by_id[state].next
        if following is None:
            raise ValueError(f"{state} est l'état final : rien ne le suit.")
        while not self.enabled(following, answers, ctx):
            nxt = self.by_id[following].next
            assert nxt is not None
            following = nxt
        return following

    def position(self, step_id: str, answers: Answers, ctx: Context) -> tuple[int, int]:
        """``(n, N)`` over the counted steps of the projected path.

        An uncounted step reports the count before it (0 for a welcome screen).
        """
        path = self.path(answers, ctx)
        counted = [s for s in path if self.by_id[s].counted]
        total = len(counted)
        if step_id in counted:
            return counted.index(step_id) + 1, total
        before = 0
        for s in self._chain():
            if s == step_id:
                break
            if s in counted:
                before += 1
        return before, total

    def reachable(self, run: Run, step_id: str, ctx: Context) -> bool:
        """The current step, or a step the visitor saw that is still on the path."""
        if step_id == self.terminal or step_id not in self.by_id:
            return False
        if step_id == self.current(run, ctx):
            return True
        return step_id in run.visited and step_id in self.path(run.answers, ctx)

    def pred(self, run: Run, step_id: str, ctx: Context) -> str | None:
        """The last step before ``step_id`` on the path that the visitor visited."""
        path = self.path(run.answers, ctx)
        if step_id not in path:
            return None
        for earlier in reversed(path[: path.index(step_id)]):
            if earlier in run.visited:
                return earlier
        return None

    def back_target(self, run: Run, step_id: str, ctx: Context) -> str | None:
        """Where the back arrow points: the review during a detour, else ``pred``."""
        if run.return_to is not None and step_id != run.return_to:
            return run.return_to
        return self.pred(run, step_id, ctx)

    def answered(self, step_id: str, answers: Answers) -> bool:
        """Every answer key present (skip values count; a step without keys is answered)."""
        return all(key in answers for key in self.by_id[step_id].answer_keys)

    def fresh(self) -> Run:
        return Run(state=self.initial)

    def before(self, step_id: str, other: str) -> bool:
        """Whether ``step_id`` precedes ``other`` in the chain (guards ignored)."""
        chain = self._chain()
        return chain.index(step_id) < chain.index(other)

    def rewind_to(self, run: Run, step_id: str) -> Run:
        """The run put back at ``step_id``: later states leave ``visited``, answers stay.

        For a run that overshot a step whose guard came back on (the account
        that crossed the gate is gone): ``current`` only walks forward, so the
        store has to move the state back explicitly.
        """
        chain = self._chain()
        keep = frozenset(s for s in run.visited if chain.index(s) < chain.index(step_id))
        return replace(run, state=step_id, visited=keep, return_to=None)

    # -- transitions (pure) ------------------------------------------------

    def check_event(
        self,
        run: Run,
        event: Event,
        ctx: Context,
        *,
        at: str,
        answer: Answers | None = None,
        target: str | None = None,
    ) -> None:
        """Raise :class:`IllegalTransition` unless ``event`` may fire at ``at``."""
        if at not in self.by_id or at == self.terminal:
            raise IllegalTransition("Cette étape n'existe pas.")
        if event is Event.EDIT:
            if at != self.review:
                raise IllegalTransition("Seul le bilan permet de modifier une réponse.")
            if not self.reachable(run, at, ctx):
                raise IllegalTransition("Le bilan n'est pas encore atteint.")
            path = self.path(run.answers, ctx)
            if (
                target is None
                or target == self.review
                or target not in run.visited
                or target not in path
                or path.index(target) >= path.index(self.review)
            ):
                raise IllegalTransition("Cette réponse ne peut pas être modifiée d'ici.")
            return
        if not self.reachable(run, at, ctx):
            raise IllegalTransition("Cette étape n'est pas encore atteinte.")
        step = self.by_id[at]
        if event is Event.SKIP:
            if step.skip_values is None:
                raise IllegalTransition("Cette étape ne se passe pas.")
            return
        foreign = set(answer or {}) - set(step.answer_keys)
        if foreign:
            raise IllegalTransition("Réponse hors sujet : " + ", ".join(sorted(foreign)) + ".")

    def apply(
        self,
        run: Run,
        event: Event,
        ctx: Context,
        *,
        at: str,
        answer: Answers | None = None,
        target: str | None = None,
    ) -> Run:
        """The run after ``event`` at ``at``; the terminal may be returned, never stored."""
        self.check_event(run, event, ctx, at=at, answer=answer, target=target)
        step = self.by_id[at]
        if event is Event.EDIT:
            assert target is not None
            # The review was seen and left: it stays reachable during the detour
            # (the back arrow of the edited step points to it).
            return replace(run, state=target, visited=run.visited | {at}, return_to=self.review)
        merged: dict[str, Any] = dict(run.answers)
        if event is Event.SKIP:
            assert step.skip_values is not None
            merged.update(step.skip_values)
        elif answer:
            merged.update(answer)
        if not self.answered(at, merged):
            raise IllegalTransition("Réponse incomplète.")
        visited = run.visited | {at}
        if event is Event.STAY:
            return replace(run, state=at, visited=visited, answers=merged)
        path = self.path(merged, ctx)
        if at == self.review:
            # An abandoned detour may have left a newly enabled step unanswered
            # (the visitor came back to the review by its back arrow): the
            # review sends them there first, and brings them back.
            for earlier in path[: path.index(at)]:
                if not self.answered(earlier, merged):
                    return replace(run, state=earlier, visited=visited, answers=merged, return_to=self.review)
        return_to = run.return_to
        if return_to is not None and (
            return_to not in path or at not in path or path.index(at) >= path.index(return_to)
        ):
            # Continuing from or after the review ends a (possibly stale) detour.
            return_to = None
        following = self.succ(at, merged, ctx)
        if return_to is not None:
            # Fill rule: an edit may have put a new step on the path; stop there,
            # otherwise go straight back to the review.
            state = following
            while state != return_to:
                if not self.answered(state, merged):
                    return replace(run, state=state, visited=visited, answers=merged, return_to=return_to)
                state = self.succ(state, merged, ctx)
            return replace(run, state=return_to, visited=visited, answers=merged, return_to=None)
        return replace(run, state=following, visited=visited, answers=merged, return_to=None)

    # -- integrity -----------------------------------------------------------

    def check(self) -> None:
        """Every structural property the projections rely on; ``ValueError`` listing all breaches."""
        problems: list[str] = []
        ids = [step.id for step in self.steps]
        if len(set(ids)) != len(ids):
            problems.append("identifiants en double : " + ", ".join(sorted({i for i in ids if ids.count(i) > 1})))
        slugs = [step.slug for step in self.steps if step.id != self.terminal]
        if any(not slug for slug in slugs):
            problems.append("chaque étape (sauf la finale) a un segment d'URL")
        if len(set(slugs)) != len(slugs):
            problems.append("segments d'URL en double : " + ", ".join(sorted({s for s in slugs if slugs.count(s) > 1})))
        for name, value in (("initial", self.initial), ("review", self.review), ("terminal", self.terminal)):
            if value not in self.by_id:
                problems.append(f"{name} = {value!r} n'est pas une étape")
        if problems:
            raise ValueError("Machine invalide : " + " ; ".join(problems))

        for step in self.steps:
            if step.next is not None and step.next not in self.by_id:
                problems.append(f"{step.id}.next = {step.next!r} n'existe pas")
        terminals = [step.id for step in self.steps if step.next is None]
        if terminals != [self.terminal]:
            problems.append(f"exactement une étape finale attendue ({self.terminal}) ; trouvé : {terminals}")
        terminal = self.by_id[self.terminal]
        if terminal.guard is not None or terminal.counted or terminal.answer_keys or terminal.kind is not Kind.TERMINAL:
            problems.append("l'étape finale doit être sans garde, non comptée, sans réponse et de type terminal")
        if any(step.kind is Kind.TERMINAL and step.id != self.terminal for step in self.steps):
            problems.append("une seule étape de type terminal")

        # The chain from ``initial`` visits every step exactly once and ends at the terminal.
        seen: list[str] = []
        current: str | None = self.initial
        while current is not None and current not in seen and current in self.by_id:
            seen.append(current)
            current = self.by_id[current].next
        if current is not None:
            problems.append(f"la chaîne boucle sur {current}")
        elif seen[-1:] != [self.terminal]:
            problems.append("la chaîne ne se termine pas sur l'étape finale")
        missing = [i for i in ids if i not in seen]
        if missing:
            problems.append("étapes hors chaîne : " + ", ".join(missing))

        review = self.by_id[self.review]
        if review.guard is not None or review.answer_keys:
            problems.append("le bilan doit être sans garde et sans réponse")
        if self.review in seen and self.terminal in seen and seen.index(self.review) >= seen.index(self.terminal):
            problems.append("le bilan doit précéder l'étape finale")

        owners: dict[str, str] = {}
        for step in self.steps:
            for key in step.answer_keys:
                if key in owners:
                    problems.append(f"clé de réponse {key!r} partagée par {owners[key]} et {step.id}")
                owners[key] = step.id
            if step.skip_values is not None and set(step.skip_values) != set(step.answer_keys):
                problems.append(f"{step.id} : les valeurs de passage ne couvrent pas exactement ses clés")
            if step.auto_advance and step.kind is not Kind.SINGLE:
                problems.append(f"{step.id} : l'avance automatique n'a de sens que pour un choix unique")
        if problems:
            raise ValueError("Machine invalide : " + " ; ".join(problems))
