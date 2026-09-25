"""AI client — OpenRouter via the `ai` SDK (ai-python.dev).

The key is optional in every environment: with no OPENROUTER_API_KEY the client
reports disabled and callers must degrade gracefully.
"""

from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass
class AIResult:
    text: str
    model: str


def _extract_text(message: Any) -> str:
    text = getattr(message, "text", None)
    if isinstance(text, str) and text:
        return text
    chunks: list[str] = []
    for part in getattr(message, "content", None) or []:
        part_text = getattr(part, "text", None)
        if isinstance(part_text, str):
            chunks.append(part_text)
    return "".join(chunks)


class AIClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return self._settings.ai_enabled

    async def complete(self, system: str, prompt: str) -> AIResult:
        if not self.enabled:
            raise RuntimeError("AI client used while disabled")
        # Imported lazily so the SDK is only needed when AI is actually enabled.
        from ai import experimental_generate, system_message, user_message
        from ai.models import Model
        from ai.providers import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(
            name="openrouter",
            default_base_url=OPENROUTER_BASE_URL,
            api_key_value=self._settings.openrouter_api_key,
        )
        model = Model(id=self._settings.openrouter_model, provider=provider)
        message = await experimental_generate(
            model=model,
            messages=[system_message(system), user_message(prompt)],
        )
        return AIResult(text=_extract_text(message), model=self._settings.openrouter_model)
