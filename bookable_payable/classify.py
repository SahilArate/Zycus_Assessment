"""Decides, from ONE BATCH of a document's page images (at most MAX_MODEL_PAGES,
see ingestion.py), which pages in that batch are evidence of a payable, and
whether each piece of evidence looks like the START of a distinct payable or a
CONTINUATION of one that likely started earlier in the document (outside this
batch). This runs BEFORE extraction on purpose: a delivery note, statement, or
purely supporting document (see DU-05 / DU-05s) should never have its fields
extracted as if it were an invoice.

pipeline.py calls this once per batch (every page of the document ends up in
some batch — nothing is pre-dropped) and merges the segments across all
batches into payable page-groups; see process_document() there for the merge
logic. This module only ever reasons about the pages it's actually shown.

Only this step and extraction.py touch the vision LLM. Everything else in this
package is plain, testable Python.
"""
from __future__ import annotations

from typing import Any

from .llm.base import VisionClient

SYSTEM_PROMPT = """You are an expert accounts-payable clerk. You are shown a
SAMPLE of consecutive pages (a "batch") from a larger supplier document — there
may be pages before and/or after this batch that you cannot see. Your job is
only to identify which parts of THESE pages are evidence of a payable
(something that creates an obligation for the buyer to pay), and whether each
part looks like the START of a distinct, separately-billed payable or a
CONTINUATION of one that likely started on a page you haven't seen. You do not
extract line items or totals here — only classify. Respond with JSON only, no
prose, no markdown fences."""

_SCHEMA_AND_RULES = """Respond as JSON:
{
  "segments": [
    {
      "pages": [ ... the exact page numbers (from the pages listed above) this segment covers ... ],
      "is_payable": true or false,
      "new_payable": true or false,
      "invoice_number": "the invoice/credit-memo number if visible on these pages, else empty",
      "doc_type": "invoice | credit_memo | delivery_note | statement | purchase_order | remittance_advice | other",
      "reason": "one short sentence"
    }
  ]
}

Rules:
- Cover every page listed above across the segments — don't drop a page silently.
- A page belongs to only one segment.
- new_payable is true only if this segment looks like the START of a distinct
  payable (its own header / invoice number / total structure begins here). If
  you cannot tell whether a page continues an earlier payable or starts a new
  one, and no invoice number is visible on it, set new_payable to false —
  treating it as evidence for whatever payable is already open is safer than
  inventing a new one.
- invoice_number is the most reliable way to link a continuation back to its
  start — report it whenever it's visible, even on a continuation page.
- A delivery note, packing slip, statement of account, purchase order, or
  remittance advice is NOT a payable — is_payable: false for those pages.
- A credit memo IS a payable (a negative one)."""


def _build_batch_user_prompt(page_numbers: list[int]) -> str:
    where = f"page {page_numbers[0]}" if len(page_numbers) == 1 else (
        f"pages {', '.join(str(p) for p in page_numbers)}"
    )
    return (
        f"You are looking at {where} of a larger, multi-page document — there may "
        f"be pages before and/or after these you cannot see in this batch. Identify "
        f"every distinct segment of payable evidence in just these pages.\n\n"
        f"{_SCHEMA_AND_RULES}"
    )


def classify_batch(
    images_b64png: list[str], page_numbers: list[int], client: VisionClient
) -> list[dict[str, Any]]:
    """Classifies ONE batch of at most MAX_MODEL_PAGES consecutive pages.
    Returns a list of segments, each: {pages, is_payable, new_payable,
    invoice_number, doc_type, reason}. Self-corrects rather than trusting the
    model blindly: pages the model didn't mention, or mentioned twice, are
    fixed up here so every page in page_numbers is accounted for EXACTLY once —
    a page the model failed to address is treated as non-payable evidence with
    an explicit note, never silently dropped from the record."""
    result = client.extract_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=_build_batch_user_prompt(page_numbers),
        images_b64_png=images_b64png,
        max_tokens=1000,
    )
    raw_segments = result.get("segments", [])
    if not isinstance(raw_segments, list):
        raw_segments = []

    cleaned: list[dict[str, Any]] = []
    seen_pages: set[int] = set()
    for seg in raw_segments:
        if not isinstance(seg, dict):
            continue
        pages = [p for p in seg.get("pages", []) if isinstance(p, int) and p in page_numbers]
        pages = [p for p in pages if p not in seen_pages]  # a page can only belong to one segment
        if not pages:
            continue
        seen_pages.update(pages)
        cleaned.append({
            "pages": pages,
            "is_payable": bool(seg.get("is_payable", False)),
            "new_payable": bool(seg.get("new_payable", False)),
            "invoice_number": str(seg.get("invoice_number", "") or "").strip(),
            "doc_type": str(seg.get("doc_type", "other") or "other").strip().lower(),
            "reason": str(seg.get("reason", "") or "").strip(),
        })

    missing = [p for p in page_numbers if p not in seen_pages]
    if missing:
        cleaned.append({
            "pages": missing,
            "is_payable": False,
            "new_payable": False,
            "invoice_number": "",
            "doc_type": "other",
            "reason": "page not addressed by classification response",
        })
    return cleaned