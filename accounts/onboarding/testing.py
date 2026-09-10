"""Fixtures for the onboarding tests: a complete set of answers and a walker.

``DEFAULT_ANSWERS`` is what the machine stores per step; ``DEFAULT_POSTS`` is
what the browser sends for the same answers. ``walk`` drives the test client
through the flow the way a visitor would — one GET, one POST, follow the
redirect — and stops before a slug when asked.
"""

from __future__ import annotations

import io
from typing import Any

from django.urls import reverse

from accounts.onboarding.flow import machine

#: Machine-level answers, per step id.
DEFAULT_ANSWERS: dict[str, dict[str, Any]] = {
    "status": {"employment_status": "employed_open"},
    "ai_tools": {"ai_tools_used": "no"},
    "challenge": {"challenge": "too_slow"},
    "help": {"help_wanted": ["track", "documents"]},
    "titles": {"job_titles": ["Ingénieur DevOps", "Ingénieur cloud"]},
    "industries": {"industries": ["it", "consulting"], "any_industry": False},
    "experience": {"experience_level": "mid"},
    "education": {"education_level": "master"},
    "work_type": {"work_types": ["permanent", "freelance"]},
    "work_mode": {"work_mode": "hybrid"},
    "cities": {"cities": ["Nivelles", "Wavre"], "search_radius_km": 40},
    "salary": {"salary_min": 5900, "salary_period": "month"},
    "timeline": {"start_timeline": "asap"},
    "identity": {"display_name": "Lionel"},
    "cv": {
        "cv_document_id": None, "cv_text_chars": None, "cv_redactions": "",
        "cv_analyzed": False, "cv_language": "",
    },
}

#: Browser-level POST payloads for the same answers; the CV step is skipped.
DEFAULT_POSTS: dict[str, dict[str, Any]] = {
    "status": {"choice": "employed_open"},
    "ai_tools": {"choice": "no"},
    "challenge": {"choice": "too_slow"},
    "help": {"choice": ["track", "documents"]},
    "titles": {"titles": ["Ingénieur DevOps", "Ingénieur cloud"]},
    "industries": {"industries": ["it", "consulting"]},
    "experience": {"choice": "mid"},
    "education": {"choice": "master"},
    "work_type": {"choice": ["permanent", "freelance"]},
    "work_mode": {"choice": "hybrid"},
    "cities": {"cities": ["Nivelles", "Wavre"], "search_radius_km": "40"},
    "salary": {"salary_period": "month", "salary_min": "5900"},
    "timeline": {"choice": "asap"},
    "identity": {"display_name": "Lionel"},
    "cv": {"action": "skip"},
}


def all_answers() -> dict[str, Any]:
    """Every default answer merged, as ``finish_onboarding`` receives them."""
    merged: dict[str, Any] = {}
    for answer in DEFAULT_ANSWERS.values():
        merged.update(answer)
    return merged


def walk(client, *, until: str | None = None, posts: dict[str, dict[str, Any]] | None = None, name: str = "Lionel"):
    """Answer every step in turn; stop before the step whose slug is ``until``.

    Returns the last response: the page of ``until`` (200) or the redirect to
    the dashboard after the plan. ``posts`` overrides the default payloads per
    step id (``{"identity": {...}}`` for the accounts-mode gate, say).
    """
    payloads = {**DEFAULT_POSTS, "identity": {"display_name": name}, **(posts or {})}
    response = client.get(reverse("accounts:onboarding"))
    for _ in range(60):
        if response.status_code != 302:
            raise AssertionError(f"Attendu une redirection, reçu {response.status_code} : {response.content[:200]!r}")
        location = response["Location"]
        if location == reverse("tracker:dashboard"):
            return response
        slug = location.rstrip("/").rsplit("/", 1)[-1]
        step = machine.by_slug.get(slug)
        if step is None:
            raise AssertionError(f"Redirection hors parcours : {location}")
        page = client.get(location)
        if step.slug == until:
            return page
        if page.status_code != 200:
            raise AssertionError(f"{location} a répondu {page.status_code}")
        response = client.post(location, payloads.get(step.id, {}))
    raise AssertionError("Le parcours ne se termine pas.")


CV_BODY = (
    "Lionel Hubaut — ingénieur DevOps. Dix années d'expérience en infrastructure cloud, "
    "automatisation, Kubernetes, Terraform, observabilité et fiabilité de plateformes. "
    "Contact : lionel@example.org, +32 470 12 34 56, Nivelles. Formation : master en "
    "sciences de l'ingénieur industriel, orientation informatique. Langues : français, anglais."
)


def docx_bytes(text: str = CV_BODY) -> bytes:
    """A real DOCX holding ``text``, for the CV step."""
    from docx import Document as DocxDocument

    document = DocxDocument()
    for paragraph in text.split("\n"):
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class FakeAnalyzer:
    """Records what the core hands to the AI layer; ``settings.CV_ANALYZER`` names it."""

    calls: list[dict[str, Any]] = []

    def analyze_cv(self, owner, *, document_id, label, language, text) -> None:
        type(self).calls.append(
            {"owner": owner, "document_id": document_id, "label": label, "language": language, "text": text}
        )

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
