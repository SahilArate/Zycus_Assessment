"""Groq backend. Same interface as Anthropic, swapped via LLM_PROVIDER=groq.

Groq's vision-model lineup changes often — check console.groq.com/docs/vision
before relying on the model name in config.py if it's been a while.

Free/on-demand tier accounts have a real output-tokens-per-minute cap (as low as
1000 for some vision models). Rather than hoping we never hit it, we retry with a
short wait — this is standard practice for any production system calling a rate
limited API, not a workaround specific to this project.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from openai import OpenAI, RateLimitError  

from ..config import settings
from .base import VisionClient

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_MAX_RATE_LIMIT_RETRIES = 4
_RATE_LIMIT_WAIT_SECONDS = 20


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


class GroqVisionClient(VisionClient):
    def __init__(self, model: str | None = None):
        self.model = model or settings.groq_model
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY is not set — check your .env file")
        self.client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key)

    def extract_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images_b64_png: list[str],
        max_tokens: int = 900,
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for b64 in images_b64_png:
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
            )

        resp = None
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                    extra_body={"reasoning_effort": "none"},  # skip "thinking" tokens — direct extraction, not multi-step reasoning
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": content},
                    ],
                )
                break
            except RateLimitError as e:
                if "tokens per day" in str(e).lower() or "TPD" in str(e):
                    raise
                if attempt == _MAX_RATE_LIMIT_RETRIES:
                    raise
                print(f"      (rate limited, waiting {_RATE_LIMIT_WAIT_SECONDS}s...)", flush=True)
                time.sleep(_RATE_LIMIT_WAIT_SECONDS)

        text = resp.choices[0].message.content or ""
        cleaned = _strip_fences(text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(f"model did not return valid JSON: {e}\nraw: {text[:500]}") from e