"""The public core must boot and serve without the optional AI distribution."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

from django.test import SimpleTestCase


class CoreWithoutAIStartupTests(SimpleTestCase):
    def test_core_starts_migrates_and_serves_when_ai_imports_are_unavailable(self):
        """A fresh process catches startup imports hidden by an initialized test app."""
        script = textwrap.dedent(
            """
            import importlib.abc
            import sys
            from unittest.mock import patch

            blocked = {
                "jobhunt_ai", "django_q", "anthropic", "langgraph",
                "langchain_mcp_adapters",
            }

            class WithoutAI(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split(".")[0] in blocked:
                        raise ModuleNotFoundError(fullname, name=fullname)

            sys.meta_path.insert(0, WithoutAI())

            # An uninstalled extension has neither importable modules nor an
            # entry point. Hide discovery before Django reads its settings.
            with patch("importlib.metadata.entry_points", return_value=()):
                import django
                django.setup()

            from django.apps import apps
            from django.core.management import call_command
            from django.test import Client
            from django.urls import reverse
            from accounts.testing import make_user
            from jobhunt.plugins import get_plugins
            from tracker.adapters import cv_analyzer
            from tracker.models import Application, Company

            assert get_plugins() == ()
            assert not apps.is_installed("jobhunt_ai")
            assert not apps.is_installed("django_q")
            call_command("check", verbosity=0)
            call_command("migrate", verbosity=0, interactive=False)

            user = make_user("Core user")
            company = Company.objects.create(owner=user, name="Example")
            application = Application.objects.create(
                owner=user, company=company, title="Engineer",
            )
            client = Client()
            client.force_login(user)
            for route in (
                "dashboard", "pipeline", "application_list",
                "document_library", "insights",
            ):
                response = client.get(reverse(f"tracker:{route}"))
                assert response.status_code == 200, (route, response.status_code)
                assert b"/copilote/" not in response.content
            assert client.get(application.get_absolute_url()).status_code == 200
            assert cv_analyzer() is None
            assert "jobhunt.ai_integration" not in sys.modules
            assert not any(name.split(".")[0] in blocked for name in sys.modules)
            """
        )
        # Never inherit a development database, provider, or API credential.
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("JOBHUNT_")
            and key not in {"ANTHROPIC_API_KEY", "BRIGHTDATA_API_TOKEN"}
        }
        environment.update(
            DJANGO_SETTINGS_MODULE="jobhunt.settings",
            JOBHUNT_DATABASE_URL="sqlite:///:memory:",
            JOBHUNT_AUTH_MODE="local",
            JOBHUNT_DEBUG="1",
            JOBHUNT_AUTO_MIGRATE="0",
            JOBHUNT_STORAGE_PROVIDER="memory",
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
