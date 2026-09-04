"""Row-level security: PostgreSQL enforces, per row, whose data a session may see.

Layer two of the isolation model. Layer one is the application scoping every
query on the signed-in account (``Model.objects.for_user(user)``, the ports
taking an ``owner``). This package makes the database refuse what layer one
might let through: each sensitive table carries a policy comparing its owner
column with the account the session is acting for, and the runtime role is
subject to those policies.

The account is announced to PostgreSQL per transaction —
``set_config('app.current_user_id', '<id>', true)`` — never per session, so a
connection reused by the next request (persistent connections, Django's pool,
PgBouncer in transaction mode) cannot carry over a user. Three things do the
announcing: the middleware (every request), the database backend (every
outermost transaction opened inside a scope) and :func:`as_user` (background
work, commands, tests). Background work started from a request goes through
:func:`on_commit` and re-enters :func:`as_user` in its thread: a query outside
any transaction, or in a thread that never bound, reads nothing.

Public API: :func:`as_user`, :func:`on_commit`, :func:`current_user_id`,
:func:`rebind`, :func:`register`, :func:`exempt`, :func:`is_enforced`.
"""

from rls.context import GUC, as_user, current_user_id, is_enforced, on_commit, rebind
from rls.registry import Rule, exempt, register, rules

__all__ = [
    "GUC",
    "Rule",
    "as_user",
    "current_user_id",
    "exempt",
    "is_enforced",
    "on_commit",
    "rebind",
    "register",
    "rules",
]
