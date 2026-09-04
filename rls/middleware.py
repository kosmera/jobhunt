"""Every request acts for exactly one account — the one signed in — or for nobody.

Placed right after ``AuthenticationMiddleware`` (it needs ``request.user``)
and before every middleware of the project. On PostgreSQL the rest of the
request — remaining middleware, view, template rendering — runs in one
transaction that carries the account (see ``rls.context``); on SQLite only
the context variable is bound, a transaction per request would serialise
every request under ``transaction_mode=IMMEDIATE``.

Details that are easy to get wrong:

- Django turns an exception raised in a view into a response *before* it
  reaches a middleware, so a transaction owned here would commit the writes
  of a half-failed view: ``process_exception`` (called by the handler for
  view exceptions, inside the transaction) marks the transaction for
  rollback, and so does a 5xx response produced by an inner middleware.
- A sign-in during the request (local mode, chooser, signup) writes a new
  session row inside the transaction; a rollback would take it away while
  the response still names its key. The session is re-created afterwards.
- The body of a POST is parsed before the transaction opens, so a slow
  upload does not hold a connection idle in transaction (Azure kills those
  after ``idle_in_transaction_session_timeout``).

The first PostgreSQL request of a process verifies the deployment: the
runtime role must be neither superuser nor ``BYPASSRLS`` nor the owner of a
table, every registered table must carry its policy, and no readable table
may be left without one. With ``RLS_ENFORCE`` the request fails otherwise
(nothing is served with the policies silently bypassed); without it a
warning is logged once.
"""

from __future__ import annotations

import logging
import threading

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from django.utils.cache import patch_vary_headers

from rls import context, verify

logger = logging.getLogger("rls")


class RowLevelSecurityMiddleware:
    sync_capable = True
    async_capable = False

    _verified = False
    _lock = threading.Lock()

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not hasattr(request, "user"):
            raise ImproperlyConfigured(
                "RowLevelSecurityMiddleware doit suivre AuthenticationMiddleware dans MIDDLEWARE."
            )
        # First access of the lazy user: session lookup and ``auth_user`` row,
        # with nobody bound yet — the one moment that table must answer.
        user = request.user
        user_id = user.pk if user.is_authenticated else None

        if not context.is_enforced():
            with context.bound(user_id):
                return self._respond(request)

        self.verify_deployment()
        if request.method == "POST":
            request.POST  # noqa: B018 — parse the body before holding a transaction
        session = getattr(request, "session", None)
        key_before = session.session_key if session is not None else None
        request.rls_transaction = True
        with context.as_user(user_id):
            response = self._respond(request)
            if response.status_code >= 500:
                transaction.set_rollback(True)
            rolled_back = transaction.get_rollback()
        if rolled_back and session is not None and session.session_key != key_before:
            # The sign-in's session row went with the rollback: give the
            # session a fresh row (and cookie) outside the transaction.
            session.cycle_key()
        return response

    def process_exception(self, request, exception):
        # Called by the handler for an exception raised in the view, inside
        # the request's transaction and before the exception becomes a
        # response (a 404 or 403 as well as a 500): nothing of that view
        # is kept. Only the transaction this middleware opened — on SQLite
        # there is none, and a test case's own must not be touched.
        if getattr(request, "rls_transaction", False) and connection.in_atomic_block:
            transaction.set_rollback(True)
        return None

    def _respond(self, request):
        response = self.get_response(request)
        if request.headers.get("HX-Request") == "true":
            # A fragment and its page share a URL: never let a cache hand
            # one out for the other.
            patch_vary_headers(response, ("HX-Request",))
        if request.user.is_authenticated and not response.has_header("Cache-Control"):
            # Nothing of a signed-in account is kept by a shared cache, nor
            # shown by the back button after signing out.
            response.headers["Cache-Control"] = "private, no-store"
        return response

    @classmethod
    def verify_deployment(cls) -> None:
        if cls._verified:
            return
        with cls._lock:
            if cls._verified:
                return
            problems = verify.problems(connection)
            if problems:
                message = "Isolation des données (RLS) : " + " ; ".join(problems)
                if settings.RLS_ENFORCE:
                    raise ImproperlyConfigured(message)
                logger.warning("%s — JOBHUNT_RLS_ENFORCE=1 refuserait de servir.", message)
            cls._verified = True

    @classmethod
    def reset_verification(cls) -> None:
        """Tests only: forget the result of the first request's verification."""
        cls._verified = False
