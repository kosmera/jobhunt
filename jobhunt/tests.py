"""Tests for the engine-as-configuration layer (``jobhunt.database``).

Pure functions, so ``SimpleTestCase`` throughout: no database is touched,
whichever engine the suite itself happens to run on. Discovered by
``manage.py test jobhunt`` even though ``jobhunt`` is not an installed app.
"""

from __future__ import annotations

import contextlib
import os
import warnings
from pathlib import Path
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from jobhunt.database import (
    CONN_MAX_AGE_ENV,
    POSTGRESQL_ENGINE,
    SQLITE_ENGINE,
    auto_migrate_default,
    conn_max_age_from_env,
    database_config,
    is_local_host,
)
from tracker import checks

# Deliberately not the current directory: relative SQLite paths must anchor on
# the project, not on wherever the process was started.
BASE_DIR = Path("/srv/jobhunt-elsewhere")
TODAYS_SQLITE_OPTIONS = {"transaction_mode": "IMMEDIATE", "init_command": "PRAGMA journal_mode=WAL;"}
AZURE = "postgres://jobhunt:secret@monserveur.postgres.database.azure.com:5432/jobhunt"


class SQLiteConfigTests(SimpleTestCase):
    def setUp(self):
        self.assertNotEqual(str(BASE_DIR), os.getcwd())

    def test_default_url_is_the_project_database_with_todays_options(self):
        config = database_config(f"sqlite:///{BASE_DIR / 'db.sqlite3'}", base_dir=BASE_DIR)
        self.assertEqual(
            config,
            {
                "ENGINE": SQLITE_ENGINE,
                "NAME": str(BASE_DIR / "db.sqlite3"),
                "OPTIONS": TODAYS_SQLITE_OPTIONS,
                "CONN_MAX_AGE": 0,
            },
        )

    def test_relative_path_is_anchored_on_base_dir(self):
        config = database_config("sqlite:///rel.sqlite3", base_dir=BASE_DIR)
        self.assertEqual(config["NAME"], str(BASE_DIR / "rel.sqlite3"))
        self.assertIsInstance(config["NAME"], str)

    def test_nested_relative_path(self):
        config = database_config("sqlite:///data/rel.sqlite3", base_dir=BASE_DIR)
        self.assertEqual(config["NAME"], str(BASE_DIR / "data" / "rel.sqlite3"))

    def test_absolute_path_keeps_four_slashes_meaning(self):
        config = database_config("sqlite:////abs/x.sqlite3", base_dir=BASE_DIR)
        self.assertEqual(config["NAME"], "/abs/x.sqlite3")

    def test_memory_is_a_plain_string(self):
        config = database_config("sqlite:///:memory:", base_dir=BASE_DIR)
        self.assertEqual(config["NAME"], ":memory:")
        self.assertIsInstance(config["NAME"], str)

    def test_memory_without_the_third_slash_is_accepted(self):
        # ``urlsplit("sqlite://:memory:").port`` raises; the sqlite branch
        # must never touch it.
        config = database_config("sqlite://:memory:", base_dir=BASE_DIR)
        self.assertEqual(config["NAME"], ":memory:")

    def test_missing_path_is_a_configuration_error(self):
        for url in ("sqlite://", "sqlite:///", "sqlite://rel.sqlite3", "sqlite:rel.sqlite3"):
            with self.subTest(url=url), self.assertRaises(ImproperlyConfigured):
                database_config(url, base_dir=BASE_DIR)

    def test_conn_max_age_is_ignored(self):
        config = database_config("sqlite:///rel.sqlite3", base_dir=BASE_DIR, conn_max_age=600)
        self.assertEqual(config["CONN_MAX_AGE"], 0)


class PostgreSQLConfigTests(SimpleTestCase):
    def test_full_url_with_query(self):
        config = database_config(
            "postgresql://alice:pw@db.example.com:6543/jobhunt?sslmode=verify-full&connect_timeout=5",
            base_dir=BASE_DIR,
        )
        self.assertEqual(
            config,
            {
                "ENGINE": POSTGRESQL_ENGINE,
                "NAME": "jobhunt",
                "USER": "alice",
                "PASSWORD": "pw",
                "HOST": "db.example.com",
                "PORT": "6543",
                "CONN_MAX_AGE": 60,
                "CONN_HEALTH_CHECKS": True,
                "OPTIONS": {
                    "sslmode": "verify-full",
                    "connect_timeout": "5",
                    "application_name": "jobhunt",
                },
            },
        )

    def test_both_schemes(self):
        for scheme in ("postgres", "postgresql"):
            with self.subTest(scheme=scheme):
                config = database_config(f"{scheme}://u:p@localhost/db", base_dir=BASE_DIR)
                self.assertEqual(config["ENGINE"], POSTGRESQL_ENGINE)

    def test_port_absent_is_empty_string(self):
        config = database_config("postgres://u:p@localhost/db", base_dir=BASE_DIR)
        self.assertEqual(config["PORT"], "")

    def test_bad_port_is_a_configuration_error(self):
        with self.assertRaises(ImproperlyConfigured):
            database_config("postgres://u:p@localhost:abc/db", base_dir=BASE_DIR)

    def test_credentials_are_percent_decoded(self):
        config = database_config(
            "postgres://us%40er:p%40ss%3Aw%2Fo%23r%25d@localhost:5432/jobhunt", base_dir=BASE_DIR
        )
        self.assertEqual(config["USER"], "us@er")
        self.assertEqual(config["PASSWORD"], "p@ss:w/o#r%d")

    def test_missing_credentials_are_empty_strings(self):
        config = database_config("postgres://localhost/jobhunt", base_dir=BASE_DIR)
        self.assertEqual(config["USER"], "")
        self.assertEqual(config["PASSWORD"], "")

    def test_remote_host_defaults_to_tls(self):
        config = database_config(AZURE, base_dir=BASE_DIR)
        self.assertEqual(config["OPTIONS"]["sslmode"], "require")

    def test_local_host_gets_no_sslmode(self):
        for host in ("localhost", "127.0.0.1", "[::1]", ""):
            with self.subTest(host=host):
                config = database_config(f"postgres://u:p@{host}/db", base_dir=BASE_DIR)
                self.assertNotIn("sslmode", config["OPTIONS"])

    def test_explicit_sslmode_is_kept_on_a_remote_host(self):
        config = database_config(f"{AZURE}?sslmode=disable", base_dir=BASE_DIR)
        self.assertEqual(config["OPTIONS"]["sslmode"], "disable")

    def test_conn_max_age_default_and_override(self):
        self.assertEqual(database_config(AZURE, base_dir=BASE_DIR)["CONN_MAX_AGE"], 60)
        self.assertEqual(
            database_config(AZURE, base_dir=BASE_DIR, conn_max_age=0)["CONN_MAX_AGE"], 0
        )
        # Django's "unlimited" must stay expressible.
        self.assertIsNone(database_config(AZURE, base_dir=BASE_DIR, conn_max_age=None)["CONN_MAX_AGE"])

    def test_pool_on_enables_pooling_and_forbids_persistent_connections(self):
        for value in ("1", "true", "yes", "on", "TRUE"):
            with self.subTest(value=value):
                config = database_config(f"{AZURE}?pool={value}", base_dir=BASE_DIR, conn_max_age=600)
                self.assertIs(config["OPTIONS"]["pool"], True)
                self.assertEqual(config["CONN_MAX_AGE"], 0)

    def test_pool_off_omits_the_key(self):
        for value in ("0", "false", "no", "off"):
            with self.subTest(value=value):
                config = database_config(f"{AZURE}?pool={value}", base_dir=BASE_DIR)
                self.assertNotIn("pool", config["OPTIONS"])
                self.assertEqual(config["CONN_MAX_AGE"], 60)

    def test_pool_garbage_is_a_configuration_error(self):
        with self.assertRaises(ImproperlyConfigured):
            database_config(f"{AZURE}?pool=maybe", base_dir=BASE_DIR)

    def test_other_query_keys_pass_verbatim_as_strings(self):
        config = database_config(
            f"{AZURE}?sslrootcert=%2Fetc%2Fssl%2Fazure.pem&connect_timeout=10"
            "&options=-c%20statement_timeout%3D5000&target_session_attrs=read-write",
            base_dir=BASE_DIR,
        )
        options = config["OPTIONS"]
        self.assertEqual(options["sslrootcert"], "/etc/ssl/azure.pem")
        self.assertEqual(options["connect_timeout"], "10")
        self.assertEqual(options["options"], "-c statement_timeout=5000")
        self.assertEqual(options["target_session_attrs"], "read-write")

    def test_last_query_value_wins_and_blanks_are_dropped(self):
        config = database_config(
            f"{AZURE}?connect_timeout=5&connect_timeout=9&sslrootcert=", base_dir=BASE_DIR
        )
        self.assertEqual(config["OPTIONS"]["connect_timeout"], "9")
        self.assertNotIn("sslrootcert", config["OPTIONS"])

    def test_application_name_default_and_override(self):
        self.assertEqual(
            database_config(AZURE, base_dir=BASE_DIR)["OPTIONS"]["application_name"], "jobhunt"
        )
        config = database_config(f"{AZURE}?application_name=jobhunt-worker", base_dir=BASE_DIR)
        self.assertEqual(config["OPTIONS"]["application_name"], "jobhunt-worker")

    def test_database_name_is_percent_decoded(self):
        config = database_config("postgres://u:p@localhost/job%20hunt", base_dir=BASE_DIR)
        self.assertEqual(config["NAME"], "job hunt")


class DatabaseConfigCommonTests(SimpleTestCase):
    def test_unknown_scheme_is_a_configuration_error(self):
        for url in ("mysql://u:p@localhost/db", "db.sqlite3", ""):
            with self.subTest(url=url), self.assertRaises(ImproperlyConfigured):
                database_config(url, base_dir=BASE_DIR)

    def test_a_fresh_dict_per_call(self):
        # Django mutates OPTIONS in place once a connection is configured.
        for url in ("sqlite:///rel.sqlite3", AZURE):
            with self.subTest(url=url):
                first = database_config(url, base_dir=BASE_DIR)
                first["OPTIONS"]["poisoned"] = True
                first["NAME"] = "changed"
                second = database_config(url, base_dir=BASE_DIR)
                self.assertIsNot(first, second)
                self.assertIsNot(first["OPTIONS"], second["OPTIONS"])
                self.assertNotIn("poisoned", second["OPTIONS"])
                self.assertNotEqual(second["NAME"], "changed")


class ConnMaxAgeFromEnvTests(SimpleTestCase):
    def test_unset_or_blank_gives_the_default(self):
        self.assertEqual(conn_max_age_from_env(environ={}), 60)
        self.assertEqual(conn_max_age_from_env(environ={CONN_MAX_AGE_ENV: "  "}), 60)
        self.assertEqual(conn_max_age_from_env(default=5, environ={}), 5)

    def test_env_override(self):
        self.assertEqual(conn_max_age_from_env(environ={CONN_MAX_AGE_ENV: "600"}), 600)
        self.assertEqual(conn_max_age_from_env(environ={CONN_MAX_AGE_ENV: "0"}), 0)
        self.assertIsNone(conn_max_age_from_env(environ={CONN_MAX_AGE_ENV: "none"}))
        self.assertIsNone(conn_max_age_from_env(environ={CONN_MAX_AGE_ENV: "None"}))

    def test_garbage_raises(self):
        for raw in ("soon", "1.5", "-1", "60s"):
            with self.subTest(raw=raw), self.assertRaises(ImproperlyConfigured):
                conn_max_age_from_env(environ={CONN_MAX_AGE_ENV: raw})

    def test_reads_the_process_environment_by_default(self):
        with mock.patch.dict(os.environ, {CONN_MAX_AGE_ENV: "120"}):
            self.assertEqual(conn_max_age_from_env(), 120)


class HostHelperTests(SimpleTestCase):
    def test_is_local_host(self):
        for host in ("", "localhost", "LOCALHOST", "127.0.0.1", "::1", "[::1]", None, " localhost "):
            with self.subTest(host=host):
                self.assertTrue(is_local_host(host))
        for host in ("monserveur.postgres.database.azure.com", "10.0.0.5", "127.0.0.2", "db"):
            with self.subTest(host=host):
                self.assertFalse(is_local_host(host))

    def test_auto_migrate_default(self):
        self.assertTrue(auto_migrate_default(True, ""))
        self.assertTrue(auto_migrate_default(True, "localhost"))
        self.assertTrue(auto_migrate_default(True, "127.0.0.1"))
        self.assertFalse(auto_migrate_default(True, "monserveur.postgres.database.azure.com"))
        self.assertFalse(auto_migrate_default(False, ""))
        self.assertFalse(auto_migrate_default(False, "monserveur.postgres.database.azure.com"))


SQLITE = {"default": {"ENGINE": SQLITE_ENGINE, "NAME": ":memory:"}}
POSTGRESQL = {"default": {"ENGINE": POSTGRESQL_ENGINE, "NAME": "jobhunt"}}


@contextlib.contextmanager
def fake_database(databases: dict, mode: str):
    """Point ``settings.DATABASES`` at an engine the check can inspect.

    Django warns that overriding ``DATABASES`` "can lead to unexpected
    behavior" because open connections keep their old settings; the check
    only reads ``ENGINE`` and opens nothing, so the warning is noise here.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "Overriding setting DATABASES", UserWarning)
        with override_settings(DATABASES=databases, AUTH_MODE=mode):
            yield


class CheckDatabaseTests(SimpleTestCase):
    def test_warns_only_in_accounts_mode_on_sqlite(self):
        with fake_database(SQLITE, "accounts"):
            ids = [problem.id for problem in checks.check_database(None)]
        self.assertEqual(ids, ["tracker.W001"])

    def test_silent_otherwise(self):
        cases = [
            (SQLITE, "local"),
            (POSTGRESQL, "accounts"),
            (POSTGRESQL, "local"),
        ]
        for databases, mode in cases:
            with self.subTest(engine=databases["default"]["ENGINE"], mode=mode):
                with fake_database(databases, mode):
                    self.assertEqual(checks.check_database(None), [])

    def test_registered_under_the_plain_tracker_tag(self):
        """``runserver`` skips checks tagged ``database``; this one must run there."""
        from django.core.checks.registry import registry

        self.assertIn(checks.check_database, registry.registered_checks)
        self.assertEqual(checks.check_database.tags, ("tracker",))
