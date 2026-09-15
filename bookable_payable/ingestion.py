"""PDF -> list of base64 PNG page images. Every document in this kit is treated
as page images and handed to the vision model — the brief tells us most of them
render as scanned pages, so we don't special-case a "native text" path that would
only work for a minority of files and add branching we'd have to maintain.
"""
from __future__ import annotations

import base64
from pathlib import Path

import pymupdf as fitz

from .config import settings

# Groq's vision models cap how many images one request can carry (as low as 3 for
# the model we use). We used to apply this cap to the WHOLE document before
# classification ever ran — which meant a 20-page document only ever had 3 of its
# pages seen at all. That's fixed now: render_pdf_pages() + batch_pages() below
# put every page in front of the model, in batches of at most this many. The cap
# only re-appears later, per payable, once we know which of ITS OWN pages are
# relevant (see select_pages_for_model below and pipeline.py).
MAX_MODEL_PAGES = 3


def select_pages_for_model(pages: list[str], max_pages: int = MAX_MODEL_PAGES) -> list[str]:
    """Caps a list of page images down to max_pages, keeping first + last (totals
    are usually near the end). Used in pipeline.py to select which of a SINGLE
    payable's own real pages go to extraction, when that payable spans more pages
    than one model call can carry — not to cap the whole document anymore."""
    if len(pages) <= max_pages:
        return pages
    n_from_end = max(1, max_pages // 2)
    n_from_start = max_pages - n_from_end
    return pages[:n_from_start] + pages[-n_from_end:]


def batch_pages(pages: list[str], batch_size: int = MAX_MODEL_PAGES) -> list[list[str]]:
    """Splits ALL of a document's rendered pages into consecutive batches of at
    most batch_size images each. Every page ends up in exactly one batch — this
    is what lets classification see the whole document instead of a 3-page guess."""
    return [pages[i:i + batch_size] for i in range(0, len(pages), batch_size)]


def render_pdf_pages(pdf_path: str | Path, dpi: int | None = None) -> list[str]:
    """Return one base64-encoded PNG string per page in the PDF."""
    dpi = dpi or settings.render_dpi
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    images: list[str] = []
    with fitz.open(str(pdf_path)) as doc:
        for page in doc:
            pix = page.get_pixmap(matrix=matrix)
            png_bytes = pix.tobytes("png")
            images.append(base64.b64encode(png_bytes).decode("ascii"))
    return images