"""Default provider for existing, non-SaaS plugin installations."""

from functools import lru_cache
import logging

import anthropic
import pydantic
from pydantic import BaseModel

from jobhunt_ai import conf
from jobhunt_ai.ports import LLMError, LLMRefusal, SERVICE_UNAVAILABLE, StructuredResult

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _configured_client(api_key, timeout, max_retries):
    return anthropic.Anthropic(
        api_key=api_key, timeout=timeout, max_retries=max_retries
    )


def get_client() -> anthropic.Anthropic:
    """Reuse a client only while its namespaced Django settings match."""
    if not isinstance(conf.API_KEY, str) or not conf.API_KEY.strip():
        logger.error("AI provider is not configured: set JOBHUNT_AI_API_KEY on the server.")
        raise LLMError(SERVICE_UNAVAILABLE)
    return _configured_client(conf.API_KEY, conf.LLM_TIMEOUT, conf.LLM_MAX_RETRIES)


class AnthropicAdapter:
    def __init__(self, *, client_factory):
        self.client_factory = client_factory

    def parse_structured[SchemaT: BaseModel](
        self, schema: type[SchemaT], *, system: str, content: str | list,
        max_tokens: int | None = None, run_id: int | None = None,
        owner_id: int | None = None,
    ) -> StructuredResult[SchemaT]:
        client = self.client_factory()
        try:
            response = client.messages.parse(
                model=conf.MODEL_ID,
                max_tokens=max_tokens or conf.MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=schema,
            )
        except anthropic.AuthenticationError as exc:
            logger.error("AI provider authentication failed; check the server's JOBHUNT_AI_API_KEY.")
            raise LLMError(SERVICE_UNAVAILABLE) from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Limite de débit atteinte, réessaie dans quelques minutes.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Erreur de l'API Anthropic ({exc.status_code}).") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Impossible de joindre l'API Anthropic (réseau ?).") from exc
        except pydantic.ValidationError as exc:
            # parse() valide la réponse à l'intérieur de l'appel : une sortie
            # tronquée (max_tokens) ou hors schéma arrive ici, pas plus bas.
            raise LLMError(
                "Le modèle a renvoyé une sortie structurée invalide ou tronquée : réessaie."
            ) from exc

        usage = {
            "input_tokens": response.usage.input_tokens or 0,
            "output_tokens": response.usage.output_tokens or 0,
            "model_id": conf.MODEL_ID,
        }
        if response.stop_reason == "refusal":
            detail = ""
            if response.stop_details and getattr(response.stop_details, "explanation", ""):
                detail = f" ({response.stop_details.explanation})"
            raise LLMRefusal(f"Le modèle a décliné cette requête{detail}.", **usage)
        if response.stop_reason == "max_tokens":
            raise LLMError("Réponse tronquée (max_tokens atteint) : réessaie.", **usage)
        if response.parsed_output is None:
            raise LLMError("Le modèle n'a pas produit de sortie structurée exploitable.", **usage)

        return StructuredResult(
            data=response.parsed_output,
            **usage,
        )
