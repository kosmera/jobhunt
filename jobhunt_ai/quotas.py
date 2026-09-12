"""Production quota policy; persistence and deployment mode are injected."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel

from jobhunt_ai.ports import AIPort, AIQuotaPort, LLMError, StructuredResult


class QuotaExceededException(LLMError):
    def __init__(self, *, tier: str, limit: int, period: str, reset_at: datetime):
        self.tier = tier
        self.limit = limit
        self.period = period
        self.reset_at = reset_at
        super().__init__(
            "Limite de débit atteinte. Réessaie dans une minute."
            if period == "minute" else
            "Ton quota IA mensuel est atteint."
        )

    def as_dict(self) -> dict:
        """Safe, stable payload for durable runs, JSON views and HTMX."""
        return {
            "code": "ai_quota_exceeded", "tier": self.tier,
            "limit": self.limit, "period": self.period,
            "reset_at": self.reset_at.isoformat(), "message": str(self),
            "can_upgrade": self.tier == "free" and self.period == "month",
        }


@dataclass(frozen=True)
class QuotaLimits:
    monthly_requests: int
    requests_per_minute: int

    def __post_init__(self):
        for value in (self.monthly_requests, self.requests_per_minute):
            if type(value) is not int or value < 0:
                raise ValueError("AI quota limits must be nonnegative integers.")


class ProductionQuotaProxy:
    def __init__(
        self, wrapped: AIPort, quotas: AIQuotaPort, *, enabled: Callable[[], bool]
    ):
        self.wrapped = wrapped
        self.quotas = quotas
        self.enabled = enabled

    def parse_structured[SchemaT: BaseModel](
        self, schema: type[SchemaT], *, system: str, content: str | list,
        max_tokens: int | None = None, run_id: int | None = None,
        owner_id: int | None = None,
    ) -> StructuredResult[SchemaT]:
        if self.enabled():
            if owner_id is None:
                raise ValueError("Production AI calls require owner_id.")
            # Reserve before I/O. Failures retain the reservation: a timeout
            # does not prove Azure did not process/bill the request.
            self.quotas.consume(owner_id)
        return self.wrapped.parse_structured(
            schema, system=system, content=content, max_tokens=max_tokens,
            run_id=run_id, owner_id=owner_id,
        )
