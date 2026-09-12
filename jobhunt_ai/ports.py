"""Structured AI contract shared by agents, providers and quota decorators.

No Django or vendor SDK belongs at this boundary. The existing
``services.llm.parse_structured`` facade implements this same call signature.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

SERVICE_UNAVAILABLE = "Le service IA est temporairement indisponible. Réessaie plus tard."


class LLMError(RuntimeError):
    """An AI failure whose message is safe to show to the user."""

    def __init__(self, message, *, input_tokens=0, output_tokens=0, model_id=""):
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.model_id = model_id


class LLMRefusal(LLMError):
    """The provider refused the requested output."""


@dataclass
class StructuredResult[SchemaT: BaseModel]:
    data: SchemaT
    input_tokens: int
    output_tokens: int
    model_id: str = ""


@runtime_checkable
class AIPort(Protocol):
    def parse_structured[SchemaT: BaseModel](
        self, schema: type[SchemaT], *, system: str, content: str | list,
        max_tokens: int | None = None, run_id: int | None = None,
        owner_id: int | None = None,
    ) -> StructuredResult[SchemaT]: ...


class AIQuotaPort(Protocol):
    def consume(self, owner_id: int) -> None:
        """Atomically reserve one request, or raise QuotaExceededException."""
