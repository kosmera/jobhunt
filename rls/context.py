"""Which account the database session is acting for.

A context variable holds the id of the account the current work belongs to.
PostgreSQL learns it through a *transaction-local* setting,
``set_config('app.current_user_id', '<id>', true)``, that the policies compare
with each row's owner column. Transaction-local on purpose: it vanishes at
COMMIT or ROLLBACK, so a connection reused afterwards (persistent connections,
Django's psycopg pool, PgBouncer in transaction mode) can never carry a
previous user. Session-level ``SET`` would.

Three states for the variable:

- *unscoped* (the default, outside any request or :func:`as_user`): nothing
  is bound and :func:`rebind` is a no-op — a ``login()`` in a test's ``setUp``
  must not bind the test thread for the rest of the run;
- *bound to nobody* (``None``): a request from an anonymous visitor. On
  PostgreSQL the transaction announces "nobody" explicitly (``''``), so a
  value a stray session-level ``SET`` may have left on the connection is
  overridden; the policies then show nothing, except the tables marked
  ``unbound_visible`` (``auth_user``, so that sign-in can find the account);
- *bound to an account*.

The setting is sent once per transaction, not once per query: by the backend
when it opens an outermost transaction inside a scope, and explicitly here
for nested blocks. Nothing caches what the server holds — a savepoint rolled
back by anyone (Django's test case, ``set_rollback``) reverts the setting
silently, so a nested block always announces itself.

Two things the variable does NOT do: it does not reach a new thread (Python
starts threads with an empty context — background work re-enters
:func:`as_user` itself, started from :func:`on_commit`), and being bound is
not being announced: a query outside any transaction runs unbound on
PostgreSQL. Requests and :func:`as_user` always open one.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import psycopg
from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, connections, transaction

GUC = "app.current_user_id"

#: Marker for "no request or ``as_user`` scope is active".
UNSCOPED = object()

_current: ContextVar[Any] = ContextVar("rls_current_user_id", default=UNSCOPED)
#: How many scopes are open — a sign-in may only rebind the outermost one.
_depth: ContextVar[int] = ContextVar("rls_scope_depth", default=0)


def user_id_of(user_or_id) -> int | None:
    """The account id behind a user instance, an int, or nothing."""
    if user_or_id is None:
        return None
    if isinstance(user_or_id, int):
        return user_or_id
    pk = getattr(user_or_id, "pk", None)
    if pk is None:
        # An anonymous user, or an unsaved instance: nobody.
        return None
    if getattr(user_or_id, "is_authenticated", True) is False:
        return None
    return int(pk)


def current_user_id() -> int | None:
    """The bound account id, or ``None`` when nobody is bound (or no scope)."""
    value = _current.get()
    return None if value is UNSCOPED else value


def is_scoped() -> bool:
    return _current.get() is not UNSCOPED


def is_enforced(using: str = DEFAULT_DB_ALIAS) -> bool:
    """Whether the database behind ``using`` enforces the policies (PostgreSQL)."""
    return connections[using].vendor == "postgresql"


@contextmanager
def bound(user_or_id):
    """Open a scope on the context variable only — no database traffic.

    What the middleware uses on SQLite, and what :func:`as_user` builds on.
    Not enough on PostgreSQL by itself: see the module docstring.
    """
    user_id = user_id_of(user_or_id)
    token = _current.set(user_id)
    depth = _depth.set(_depth.get() + 1)
    try:
        yield user_id
    finally:
        _depth.reset(depth)
        _current.reset(token)


def apply(connection, user_id: int | None) -> None:
    """Announce ``user_id`` (``None``: nobody) to the transaction open on ``connection``.

    Goes through the driver with a server-bound parameter, outside Django's
    cursor wrapper: the statement is neither interpolated into the query
    text the server logs nor counted by ``connection.queries`` /
    ``assertNumQueries`` — it is plumbing, not a query of the page. Callers
    guarantee a transaction is open (``set_config(..., true)`` outside one
    is a no-op).
    """
    connection.ensure_connection()
    value = "" if user_id is None else str(user_id)
    with connection.wrap_database_errors:
        with psycopg.Cursor(connection.connection) as cursor:
            cursor.execute("SELECT set_config(%s, %s, true)", (GUC, value))


@contextmanager
def as_user(user_or_id, *, using: str = DEFAULT_DB_ALIAS):
    """Run the block on behalf of ``user_or_id`` (``None``: on behalf of nobody).

    Binds the context variable and, on PostgreSQL, opens a transaction (a
    savepoint when one is already open) carrying the account. On leaving a
    nested block the enclosing scope's account is announced again — the
    setting would otherwise outlive the block, until the end of the
    enclosing transaction. A block that raises needs no restoring:
    PostgreSQL undoes the setting with the savepoint.
    """
    user_id = user_id_of(user_or_id)
    connection = connections[using]
    previous = current_user_id()
    with bound(user_id):
        if connection.vendor != "postgresql":
            yield user_id
            return
        outermost = not connection.in_atomic_block
        with transaction.atomic(using=using):
            # The backend announces the account itself when it opens the
            # outermost transaction; a nested block, or a stock backend, must.
            if not (outermost and getattr(connection, "rls_tracks_transactions", False)):
                apply(connection, user_id)
            yield user_id
            if not outermost and previous != user_id and not connection.needs_rollback:
                apply(connection, previous)


def rebind(user_or_id, *, using: str = DEFAULT_DB_ALIAS) -> None:
    """Change the account of the current scope — after a sign-in mid-request.

    Local mode signs the visitor in from the accounts middleware, the chooser
    and the signup views call ``auth.login``: the rest of that request must
    act for the new account. Outside a scope (a test's ``force_login``, a
    shell) nothing happens. Inside a *nested* scope it is refused: the
    enclosing block would restore its own account on exit and the sign-in
    would silently evaporate — sign in after the block instead.
    """
    if not is_scoped():
        return
    if _depth.get() > 1:
        raise ImproperlyConfigured(
            "rls : connexion (auth.login) à l'intérieur d'un bloc as_user imbriqué ; "
            "le compte serait perdu à la sortie du bloc. Connecte après le bloc."
        )
    user_id = user_id_of(user_or_id)
    _current.set(user_id)
    connection = connections[using]
    if connection.vendor == "postgresql" and connection.in_atomic_block:
        apply(connection, user_id)


def on_commit(func: Callable[[], Any], *, using: str = DEFAULT_DB_ALIAS) -> None:
    """``transaction.on_commit`` that keeps acting for the current account.

    A raw ``on_commit`` callback runs after COMMIT, when the announcement
    has expired: on PostgreSQL it would read nothing and could write
    nothing. Start background work from here (a thread does not inherit
    the context either: it re-enters ``as_user`` itself).
    """
    user_id = current_user_id()

    def run():
        with as_user(user_id, using=using):
            func()

    transaction.on_commit(run, using=using)
