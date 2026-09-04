"""Run the test suite the way production runs: as the application role.

On PostgreSQL the test database is created and migrated by the superuser of
the connection — which bypasses every policy. So each request made through
the test client switches to the application role for its duration
(``SET ROLE`` … ``RESET ROLE``, through the driver so the two statements do
not count in ``assertNumQueries``), while the test body keeps the superuser
to set fixtures up for several accounts. Every existing page test thereby
exercises the policies for free; on SQLite nothing changes. A test whose
body must run as the runtime role too (a management command, a signal)
mixes in :class:`AppRoleTestCase`.

``TEST_RUNNER = "rls.testing.TestRunner"`` installs the client on
``SimpleTestCase`` for the whole run, extensions included, and turns
``RLS_ENFORCE`` off for the run: the first-request verification would refuse
a development database where an installed extension has not registered its
tables yet. Tests that exercise enforcement opt back in with
``override_settings(RLS_ENFORCE=True)``.
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, connections
from django.test import Client, SimpleTestCase
from django.test.runner import DiscoverRunner

from rls import sql


@contextmanager
def app_role(using: str = DEFAULT_DB_ALIAS):
    """Act as the application role for the block (PostgreSQL only)."""
    connection = connections[using]
    if connection.vendor != "postgresql":
        yield
        return
    role = sql.role_name(settings.RLS_APP_ROLE)
    connection.ensure_connection()
    try:
        connection.connection.execute(f"SET ROLE {role}").close()
    except Exception as exc:  # psycopg InsufficientPrivilege / UndefinedObject
        raise RuntimeError(
            f"La suite ne peut pas prendre le rôle {role} ({exc}). Sur PostgreSQL les requêtes "
            "de test tournent avec le rôle applicatif : le rôle de connexion doit pouvoir "
            "SET ROLE (superutilisateur, ou GRANT ... WITH SET TRUE par la migration rls)."
        ) from exc
    try:
        yield
    finally:
        if connection.connection is not None:
            connection.connection.execute("RESET ROLE").close()


class AppRoleClient(Client):
    def request(self, **request):
        with app_role():
            return super().request(**request)


class AppRoleTestCase:
    """Mixin: the test body itself runs as the application role on PostgreSQL.

    Fixtures created in ``setUpTestData`` still come from the superuser.
    """

    def setUp(self):
        super().setUp()  # type: ignore[misc]
        role = app_role()
        role.__enter__()
        self.addCleanup(role.__exit__, None, None, None)  # type: ignore[attr-defined]


class TestRunner(DiscoverRunner):
    """The project's runner: the application role, and a throwaway MEDIA_ROOT.

    Uploads reach the storage for real now (the use cases write through the
    port), so the run gets a temporary directory of its own: a test that
    forgets to pass the in-memory storage leaves its files there and not in
    the developer's ``media/``.
    """

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        SimpleTestCase.client_class = AppRoleClient
        settings.RLS_ENFORCE = False
        self._media_root = tempfile.mkdtemp(prefix="jobhunt-test-media-")
        settings.MEDIA_ROOT = self._media_root

    def teardown_test_environment(self, **kwargs):
        super().teardown_test_environment(**kwargs)
        shutil.rmtree(getattr(self, "_media_root", ""), ignore_errors=True)
