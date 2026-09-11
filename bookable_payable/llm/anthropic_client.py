from __future__ import annotations

import json
import re
from typing import Any

import anthropic

from ..config import settings
from .base import VisionClient

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


class AnthropicVisionClient(VisionClient):
    def __init__(self, model: str | None = None):
        self.model = model or settings.anthropic_model
        self.client = anthropic.Anthropic()

    def extract_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images_b64_png: list[str],
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        for b64 in images_b64_png:
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64},
                }
            )
        content.append({"type": "text", "text": user_prompt})

        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        cleaned = _strip_fences(text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(f"model did not return valid JSON: {e}\nraw: {text[:500]}") from e
