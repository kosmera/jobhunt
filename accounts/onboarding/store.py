"""Where a run lives while it is in flight: the session, and nothing else.

The session is the one place an anonymous visitor may keep state under the
row-level policies (``django_session`` is bookkeeping the application role
reads and writes; every account table hides its rows from a session bound
to nobody). It also crosses the identity gate: Django's ``login()`` cycles
the session key and keeps its data. Rows are written only when the account
exists — at the gate and at completion — and the terminal state is never
stored, so a completion that fails leaves the run on the last screen.
"""

from __future__ import annotations

from accounts.onboarding.flow import machine
from accounts.onboarding.machine import Run

SESSION_KEY = "onboarding"


def load(request) -> Run | None:
    """The run in the session, validated against the machine; ``None`` when absent or stale.

    Never writes: a GET from a crawler creates no session row.
    """
    run = Run.from_json(request.session.get(SESSION_KEY))
    if run is None:
        return None
    ids = set(machine.by_id)
    if run.state not in ids or run.state == machine.terminal:
        return None
    if not run.visited <= ids:
        return None
    if run.return_to is not None and run.return_to not in ids:
        return None
    return run


def save(request, run: Run) -> None:
    assert run.state != machine.terminal, "l'état final ne se stocke pas"
    # Assignment marks the session modified; mutating the nested dict would not.
    request.session[SESSION_KEY] = run.to_json()


def clear(request) -> None:
    request.session.pop(SESSION_KEY, None)
