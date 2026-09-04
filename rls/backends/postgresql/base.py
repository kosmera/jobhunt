"""Django's PostgreSQL backend, plus one habit: every outermost transaction
opened inside a scope announces the account it acts for — or that it acts
for nobody.

``ENGINE = "rls.backends.postgresql"``. Nothing else changes — features,
introspection, schema editor, pooling and ``assume_role`` are the stock ones.

The hook sits in ``_set_autocommit``: it is what ``transaction.atomic()``
calls to open the outermost transaction (nested blocks are savepoints and
never come here), after ``ensure_connection()``. psycopg 3 only sends
``BEGIN`` with the first statement, so the ``set_config`` issued right after
switching autocommit off lands inside the new transaction and expires with
it. "Nobody" is announced too (``''``): a value left on the connection by a
session-level ``SET`` — startup ``options``, an ad-hoc psql session through
PgBouncer — would otherwise make an anonymous request act for that account.

Two consequences worth knowing: the hook does not fire when autocommit is
already off when the block is entered (``AUTOCOMMIT=False`` in
``DATABASES`` — refused by the checks — or a test case's outer transaction,
which ``rls.as_user`` covers by announcing the account itself), and a query
run in autocommit mode is unbound: the policies then show nothing.
"""

from __future__ import annotations

from django.db.backends.postgresql import base as postgresql

from rls import context


class DatabaseWrapper(postgresql.DatabaseWrapper):
    #: Tells ``rls.context`` that outermost transactions announce themselves.
    rls_tracks_transactions = True

    def _set_autocommit(self, autocommit):
        super()._set_autocommit(autocommit)  # pyright: ignore[reportAttributeAccessIssue]
        if autocommit or not context.is_scoped():
            return
        try:
            context.apply(self, context.current_user_id())
        except Exception:
            # Django has not recorded the switch yet: left as is, the driver
            # would be in manual-commit mode with Django believing otherwise,
            # and every later statement would open a transaction nobody
            # commits. Drop the connection instead; the next use reconnects.
            self.close()
            raise
