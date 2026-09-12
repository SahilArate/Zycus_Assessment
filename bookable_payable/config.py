"""Central config. One switch decides which vision backend the whole pipeline uses.

Reads from a local .env file (never committed to git) or real environment
variables — same names either way:
    LLM_PROVIDER=anthropic   (default; works inside Claude's sandbox)
    LLM_PROVIDER=groq        (your own key, for running this for real)

    ANTHROPIC_API_KEY=...
    GROQ_API_KEY=...
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # reads .env in the project root, if present; harmless if it's not


@dataclass(frozen=True)
class Settings:
    llm_provider: str = os.environ.get("LLM_PROVIDER", "anthropic")
    anthropic_model: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    groq_model: str = os.environ.get("GROQ_MODEL", "qwen/qwen3.6-27b")
    max_extraction_retries: int = int(os.environ.get("MAX_EXTRACTION_RETRIES", "2"))
    render_dpi: int = int(os.environ.get("RENDER_DPI", "150"))


settings = Settings()