from __future__ import annotations

from ..config import settings
from .base import VisionClient


def get_vision_client() -> VisionClient:
    if settings.llm_provider == "anthropic":
        from .anthropic_client import AnthropicVisionClient

        return AnthropicVisionClient()
    if settings.llm_provider == "groq":
        from .groq_client import GroqVisionClient

        return GroqVisionClient()
    raise ValueError(f"unknown LLM_PROVIDER: {settings.llm_provider}")
