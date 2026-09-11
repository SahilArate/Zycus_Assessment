"""Groq backend. Same interface as Anthropic, swapped via LLM_PROVIDER=groq.

Not runnable inside this sandbox (api.groq.com isn't reachable here) — wired and
ready for you to point at your own key when you run the pipeline locally.
Pin the exact vision model slug from https://console.groq.com/docs/models before
relying on this; Groq rotates vision model availability.
"""
from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI  # Groq exposes an OpenAI-compatible API surface

from ..config import settings
from .base import VisionClient

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


class GroqVisionClient(VisionClient):
    def __init__(self, model: str | None = None):
        self.model = model or settings.groq_model
        self.client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=None)  # reads GROQ_API_KEY

    def extract_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images_b64_png: list[str],
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for b64 in images_b64_png:
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
            )

        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        )
        text = resp.choices[0].message.content or ""
        cleaned = _strip_fences(text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(f"model did not return valid JSON: {e}\nraw: {text[:500]}") from e
