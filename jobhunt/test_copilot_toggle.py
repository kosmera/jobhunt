"""``COPILOT_ENABLED`` decides, at boot, whether the copilot is part of the site.

Each case boots a fresh interpreter: the flag is read once by the settings,
and an initialised test process cannot undo an ``INSTALLED_APPS`` entry.
"""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

from django.test import SimpleTestCase

PROLOGUE = """
import sys

import django

django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()  # the client's ``response.context`` needs the hooks

from django.apps import apps
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from accounts.testing import make_user
from tracker.adapters import cv_analyzer
from tracker.models import Application, Company

call_command("check", verbosity=0)
call_command("migrate", verbosity=0, interactive=False)

user = make_user("Core user")
company = Company.objects.create(owner=user, name="Example")
application = Application.objects.create(owner=user, company=company, title="Engineer")
client = Client()
client.force_login(user)
"""

WITHOUT_COPILOT = PROLOGUE + """
assert not apps.is_installed("jobhunt_ai")
assert apps.is_installed("django_q")  # email delivery also uses the shared queue
for route in ("dashboard", "pipeline", "application_list", "document_library", "insights"):
    response = client.get(reverse(f"tracker:{route}"))
    assert response.status_code == 200, (route, response.status_code)
    assert b"/copilote/" not in response.content, route
    assert response.context["copilot_enabled"] is False, route
response = client.get(application.get_absolute_url())
assert response.status_code == 200
assert b"/copilote/" not in response.content
assert cv_analyzer() is None
assert "jobhunt_ai" not in sys.modules
assert not any(name.split(".")[0] == "jobhunt_ai" for name in sys.modules)
"""

WITH_COPILOT = PROLOGUE + """
from datetime import timedelta

from django.utils import timezone

from accounts.models import Profile, SubscriptionLevel
from tracker.events import CVEventPublisher

assert apps.is_installed("jobhunt_ai")
assert apps.is_installed("django_q")
copilot = reverse("jobhunt_ai:copilot")
assert copilot == "/copilote/", copilot
response = client.get(reverse("tracker:dashboard"))
assert response.status_code == 200
assert response.context["copilot_enabled"] is True
assert copilot in response.content.decode()
assert [item["label"] for item in response.context["nav_items"]][-1] == "Copilote"
# Local accounts include Premium by default. An explicit downgrade closes it.
assert client.get(copilot).status_code == 200
Profile.objects.filter(user=user).update(subscription_level=SubscriptionLevel.FREE)
assert client.get(copilot).status_code == 402
Profile.objects.filter(user=user).update(
    subscription_level=SubscriptionLevel.PREMIUM,
    premium_until=timezone.now() + timedelta(days=30),
)
assert client.get(copilot).status_code == 200
assert isinstance(cv_analyzer(), CVEventPublisher)
"""

# A blank ``JOBHUNT_AI_API_KEY=`` line in ``.env`` must not hide the exported
# provider key the README promises is accepted.
EMPTY_KEY_FALLS_BACK = """
import django

django.setup()

from django.conf import settings

assert settings.JOBHUNT_AI_API_KEY == "sk-test", settings.JOBHUNT_AI_API_KEY
"""


class CopilotToggleTests(SimpleTestCase):
    def boot(self, script: str, *, copilot_enabled: str, **extra: str) -> None:
        # Never inherit a development database, provider, or API credential.
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("JOBHUNT_")
            and key not in {"ANTHROPIC_API_KEY", "BRIGHTDATA_API_TOKEN", "IS_SAAS_PRODUCTION"}
        }
        environment.update(
            DJANGO_SETTINGS_MODULE="jobhunt.settings",
            JOBHUNT_DATABASE_URL="sqlite:///:memory:",
            JOBHUNT_AUTH_MODE="local",
            JOBHUNT_DEBUG="1",
            JOBHUNT_AUTO_MIGRATE="0",
            JOBHUNT_STORAGE_PROVIDER="memory",
            IS_SAAS_PRODUCTION="False",
            COPILOT_ENABLED=copilot_enabled,
            **extra,
        )
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(script)],
            cwd=Path(__file__).resolve().parent.parent,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_core_boots_migrates_and_serves_without_the_copilot(self):
        self.boot(WITHOUT_COPILOT, copilot_enabled="0")

    def test_the_copilot_is_mounted_and_gated_by_premium_when_enabled(self):
        self.boot(WITH_COPILOT, copilot_enabled="1")

    def test_an_empty_provider_key_falls_back_to_the_anthropic_variable(self):
        self.boot(
            EMPTY_KEY_FALLS_BACK, copilot_enabled="1",
            JOBHUNT_AI_API_KEY="", ANTHROPIC_API_KEY="sk-test",
        )
