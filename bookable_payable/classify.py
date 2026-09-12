"""Decides, from a document's page images, whether it is a payable at all and
how many payables it contains. This runs BEFORE extraction on purpose: a
delivery note, statement, or purely supporting document (see DU-05 / DU-05s)
should never have its fields extracted as if it were an invoice.

Only this step and extraction.py (next) touch the vision LLM. Everything else
in this package is plain, testable Python.
"""
from __future__ import annotations

from typing import Any

from .llm.base import VisionClient

SYSTEM_PROMPT = """You are an expert accounts-payable clerk. You look at page
images of ONE supplier document and decide two things only: what kind of
document it is, and whether the buyer owes money because of it. You do not
extract any line items or numbers here — only classify. Respond with JSON only,
no prose, no markdown fences."""

USER_PROMPT = """Look at every page of this document and answer:

{
  "doc_type": "invoice | credit_memo | delivery_note | statement | purchase_order | remittance_advice | other",
  "is_payable": true or false,
  "payable_count": integer,
  "reason": "one short sentence — if is_payable is false, explain why this document does not create an obligation to pay; if true, explain briefly what it is"
}

Rules for your judgment:
- A delivery note, packing slip, statement of account, purchase order, or remittance
  advice is NOT itself a payable, even though it may reference money — set is_payable
  to false, payable_count to 0.
- A credit memo IS a payable (a negative one) — set is_payable true, doc_type "credit_memo".
- payable_count is almost always 1. Only set it higher if the SAME file genuinely
  contains multiple distinct, separately-billed invoices stapled together (rare —
  look for multiple distinct invoice numbers each with their own totals).
- If you are not confident this document asks to be paid, prefer is_payable: false
  with a clear reason over guessing yes."""


def classify_document(pages_b64png: list[str], client: VisionClient) -> dict[str, Any]:
    """Returns a dict with doc_type, is_payable, payable_count, reason.
    Self-corrects the one contradiction an LLM can plausibly produce (payable=true
    with count=0, or payable=false with count>0) rather than trusting it blindly."""
    result = client.extract_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=USER_PROMPT,
        images_b64_png=pages_b64png,
        max_tokens=512,
    )

    doc_type = str(result.get("doc_type", "other")).strip().lower()
    is_payable = bool(result.get("is_payable", False))
    try:
        payable_count = int(result.get("payable_count", 0))
    except (TypeError, ValueError):
        payable_count = 0
    reason = str(result.get("reason", "")).strip()

    if not is_payable:
        payable_count = 0
    elif payable_count < 1:
        payable_count = 1  # is_payable=true with count=0 is a contradiction; assume one

    return {
        "doc_type": doc_type,
        "is_payable": is_payable,
        "payable_count": payable_count,
        "reason": reason,
    }