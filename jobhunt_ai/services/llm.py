"""Backward-compatible agent entry point, with provider-neutral run accounting."""

from django.db.models import F
from pydantic import BaseModel

import rls
from jobhunt_ai.adapters import get_ai_port
from jobhunt_ai.adapters.anthropic import (
    get_client as get_client, _configured_client as _configured_client,
)
from jobhunt_ai.ports import (
    LLMError as LLMError, LLMRefusal as LLMRefusal,
    SERVICE_UNAVAILABLE as SERVICE_UNAVAILABLE, StructuredResult as StructuredResult,
)

__all__ = [
    "LLMError",
    "LLMRefusal",
    "SERVICE_UNAVAILABLE",
    "StructuredResult",
    "_configured_client",
    "get_client",
    "parse_structured",
]


def parse_structured[SchemaT: BaseModel](
    schema: type[SchemaT], *, system: str, content: str | list,
    max_tokens: int | None = None, run_id: int | None = None,
    owner_id: int | None = None,
) -> StructuredResult[SchemaT]:
    try:
        result = get_ai_port().parse_structured(
            schema, system=system, content=content, max_tokens=max_tokens,
            run_id=run_id, owner_id=owner_id,
        )
    except LLMError as exc:
        # Refusals and truncation can still consume billable tokens.
        if exc.model_id:
            _record_usage(exc, run_id=run_id, owner_id=owner_id)
        raise
    _record_usage(result, run_id=run_id, owner_id=owner_id)
    return result


def _record_usage(result, *, run_id, owner_id):
    if run_id is not None:
        from jobhunt_ai.models import AgentRun

        with rls.as_user(owner_id):
            AgentRun.objects.filter(pk=run_id, owner_id=owner_id).update(
                input_tokens=F("input_tokens") + result.input_tokens,
                output_tokens=F("output_tokens") + result.output_tokens,
                model_id=result.model_id,
            )
