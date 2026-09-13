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

# Groq's vision models cap how many images one request can carry (as low as 5 for
# some models). This kit has documents up to 20 pages, so we can't just send every
# page — we take the first few (header/summary info) and the last one or two
# (totals are often on the final page of a multi-page document). This is a
# documented simplification: a genuinely long line-item table that spans pages
# beyond what we sample here could lose rows. Worth flagging in DESIGN.md if a
# real document in the kit turns out to need more than this captures.
MAX_MODEL_PAGES = 3


def select_pages_for_model(pages: list[str], max_pages: int = MAX_MODEL_PAGES) -> list[str]:
    if len(pages) <= max_pages:
        return pages
    n_from_end = max(1, max_pages // 2)
    n_from_start = max_pages - n_from_end
    return pages[:n_from_start] + pages[-n_from_end:]

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
