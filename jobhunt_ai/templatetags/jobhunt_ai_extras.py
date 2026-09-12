"""Balises utilisées par les gabarits du copilote, y compris ceux inclus
dans les pages du cœur (qui ne connaissent pas nos modèles)."""

from __future__ import annotations

from django import template

from jobhunt_ai.access import has_copilot_access
from jobhunt_ai.models import AgentRun, GeneratedCV, MatchReport, RunStatus

register = template.Library()


@register.simple_tag(takes_context=True)
def copilot_access(context):
    request = context.get("request")
    return has_copilot_access(getattr(request, "user", None))


@register.simple_tag
def active_ai_run(application=None, user=None):
    """L'exécution en cours d'une candidature, ou de tout un compte."""
    queryset = AgentRun.objects.filter(
        status__in=[RunStatus.PENDING, RunStatus.RUNNING]
    )
    if application is not None:
        queryset = queryset.filter(application=application)
    elif user is not None:
        queryset = queryset.filter(owner=user)
    else:
        return None
    return queryset.first()


@register.simple_tag
def latest_match_report(application):
    return (
        MatchReport.objects.filter(application=application)
        .order_by("-created_at")
        .first()
    )


@register.simple_tag
def latest_generated_cv(application):
    return GeneratedCV.objects.filter(application=application).first()
