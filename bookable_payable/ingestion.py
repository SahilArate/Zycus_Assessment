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
