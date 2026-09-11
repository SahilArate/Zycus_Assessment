"""The one interface every vision backend must satisfy.

Swapping Anthropic <-> Groq <-> anything else is a matter of implementing this
one method — nothing else in the pipeline needs to know which provider is behind it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class VisionClient(ABC):
    @abstractmethod
    def extract_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images_b64_png: list[str],
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Send prompt + page images, get back a parsed JSON object.

        Implementations must:
          - request/force JSON-only output from the underlying model
          - strip markdown code fences if the model adds them anyway
          - raise ValueError if the response isn't valid JSON (never silently
            return a guessed/partial structure)
        """
        raise NotImplementedError
