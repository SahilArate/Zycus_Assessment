"""Central config. One switch decides which vision backend the whole pipeline uses.

Set via environment variables so nothing is hardcoded:
    LLM_PROVIDER=anthropic   (default; works inside this sandbox)
    LLM_PROVIDER=groq        (swap in your own key when you run this locally)

    ANTHROPIC_API_KEY=...
    GROQ_API_KEY=...
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    llm_provider: str = os.environ.get("LLM_PROVIDER", "anthropic")
    anthropic_model: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    groq_model: str = os.environ.get("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    max_extraction_retries: int = int(os.environ.get("MAX_EXTRACTION_RETRIES", "2"))
    render_dpi: int = int(os.environ.get("RENDER_DPI", "150"))


settings = Settings()
