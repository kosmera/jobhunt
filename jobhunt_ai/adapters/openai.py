"""Direct OpenAI adapter and shared structured-output handling."""

from dataclasses import dataclass
from functools import lru_cache
import logging

import openai
from pydantic import BaseModel, ValidationError

from jobhunt_ai.agents.schemas import ParsedProfile, ScoutQueries, ScrapedOffers
from jobhunt_ai.ports import LLMError, LLMRefusal, SERVICE_UNAVAILABLE, StructuredResult
from jobhunt_ai.settings import get

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelRouter:
    simple_deployment: str
    complex_deployment: str

    def route(self, schema: type[BaseModel]) -> str:
        # Explicit allowlist: unknown tasks take the capable model. No extra
        # model call to classify a request, and no prompt-controlled routing.
        if schema in (ParsedProfile, ScoutQueries, ScrapedOffers):
            return self.simple_deployment
        return self.complex_deployment


@lru_cache(maxsize=1)
def _configured_client(api_key, timeout):
    return openai.OpenAI(
        api_key=api_key, base_url="https://api.openai.com/v1",
        timeout=timeout, max_retries=0,
    )


class OpenAIAdapter:
    model_settings = ("OPENAI_SIMPLE_MODEL", "OPENAI_COMPLEX_MODEL")
    limit_prefix = "OPENAI"
    sdk_errors = (openai.OpenAIError,)
    store_completion: bool | openai.Omit = False

    def _client(self):
        key = get("OPENAI_API_KEY")
        if not key or key.startswith("@Microsoft.KeyVault("):
            logger.error("Configure the server's OpenAI credential through its secret store.")
            raise LLMError(SERVICE_UNAVAILABLE)
        return _configured_client(key, get("LLM_TIMEOUT"))

    def __init__(self, *, client=None, router: ModelRouter | None = None):
        self.client = client
        self.router = router or ModelRouter(
            get(self.model_settings[0]), get(self.model_settings[1])
        )

    def parse_structured[SchemaT: BaseModel](
        self, schema: type[SchemaT], *, system: str, content: str | list,
        max_tokens: int | None = None, run_id: int | None = None,
        owner_id: int | None = None,
    ) -> StructuredResult[SchemaT]:
        # Preserve text-block callers, but never forward raw PDF/image blocks
        # that could evade the host's existing anonymization pipeline.
        if isinstance(content, list):
            if any(
                not isinstance(block, dict) or block.get("type") != "text"
                or not isinstance(block.get("text"), str) for block in content
            ):
                raise LLMError("Le service IA attend du texte anonymisé.")
            content = "\n".join(block["text"] for block in content)
        if not isinstance(content, str):
            raise LLMError("Le service IA attend du texte anonymisé.")
        if len(system) + len(content) > get(f"{self.limit_prefix}_MAX_INPUT_CHARS"):
            raise LLMError("Le texte est trop long pour être analysé en une requête.")
        output_limit = get(f"{self.limit_prefix}_MAX_OUTPUT_TOKENS")
        if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
            raise ValueError("max_tokens must be a positive integer.")
        deployment = self.router.route(schema)
        if not deployment:
            logger.error("Configure the server's AI model names.")
            raise LLMError(SERVICE_UNAVAILABLE)
        try:
            client = self.client or self._client()
            response = client.chat.completions.parse(
                model=deployment,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
                response_format=schema,
                max_completion_tokens=min(max_tokens or get("MAX_TOKENS"), output_limit),
                store=self.store_completion,
            )
        except openai.ContentFilterFinishReasonError as exc:
            raise LLMRefusal("Le modèle a décliné cette requête.") from exc
        except openai.LengthFinishReasonError as exc:
            usage = exc.completion.usage
            raise LLMError(
                "La réponse IA est invalide ou tronquée. Réessaie.",
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                model_id=deployment,
            ) from exc
        except ValidationError as exc:
            raise LLMError("La réponse IA est invalide ou tronquée. Réessaie.") from exc
        except openai.RateLimitError as exc:
            raise LLMError("Le service IA est occupé. Réessaie dans quelques minutes.") from exc
        except self.sdk_errors as exc:
            # Never surface SDK messages containing prompts, URLs or credentials.
            logger.warning("OpenAI request failed (%s).", type(exc).__name__)
            raise LLMError(SERVICE_UNAVAILABLE) from exc
        usage = {
            "input_tokens": response.usage.prompt_tokens if response.usage else 0,
            "output_tokens": response.usage.completion_tokens if response.usage else 0,
            "model_id": deployment,
        }
        if not response.choices:
            raise LLMError("Le modèle n'a pas produit de sortie structurée exploitable.", **usage)
        choice = response.choices[0]
        if choice.message.refusal or choice.finish_reason == "content_filter":
            raise LLMRefusal("Le modèle a décliné cette requête.", **usage)
        if choice.finish_reason != "stop" or choice.message.parsed is None:
            raise LLMError("La réponse IA est invalide ou tronquée. Réessaie.", **usage)
        return StructuredResult(
            data=choice.message.parsed,
            **usage,
        )
