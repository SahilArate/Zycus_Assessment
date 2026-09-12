"""Reads the actual raw values off a document that classify.py already confirmed
is a payable. This is the single most important prompt in the system — every
number the rest of the pipeline touches comes from here.

Split into TWO smaller calls (header fields, then line items) rather than one
big call. Reason: header fields are always small and fixed-size, but line items
scale with the document (this kit alone has documents with up to 35 lines) —
splitting keeps each individual request's output small and predictable, which
matters directly on accounts with a low output-tokens-per-minute cap. It also
means a document with a huge line-item table doesn't risk truncating the header
fields that came before it in a single response.

The combined output shape matches exactly what autodraft.build_payable() expects
(see autodraft.py's _map_tax/_map_line/build_payable) — that's the contract
between these two files.
"""
from __future__ import annotations

from typing import Any

from .llm.base import VisionClient

HEADER_SYSTEM_PROMPT = """You are an expert accounts-payable clerk reading ONE
supplier document already confirmed to be a payable (an invoice or credit memo).
Extract only the HEADER-level fields — not line items, those are read separately.
Extract every value exactly as printed; never compute a number that isn't itself
printed on the page. Never invent a master-data code. Respond with JSON only, no
prose, no markdown fences."""

HEADER_USER_PROMPT = """Extract this document's HEADER fields (not line items) as JSON:

{
  "doc_type": "invoice | credit_memo",
  "invoice_number": "",
  "invoice_date": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD or empty",
  "currency": "3-letter code as printed or implied by the currency symbol",
  "supplier_name": "",
  "supplier_vat_id": "",
  "supplier_address": "",
  "supplier_country": "2-letter ISO guess from the address/VAT prefix, or empty",
  "buyer_name": "",
  "payment_term_text": "exact wording as printed, e.g. 'Net 10' or '90 days net'",
  "po_number": "",
  "header_charges": [
    {"label": "e.g. Management Fee, Handling Fee", "basis": "how it's computed in plain words, e.g. '9% of subtotal'", "amount": ""}
  ],
  "header_taxes": [
    {
      "label": "exact tax name as printed, e.g. VAT, GST, Withholding Tax",
      "rate_percent": "",
      "amount": "the printed tax amount. If this tax's base is NOT the plain line subtotal (e.g. includes another charge or tax), give the exact printed amount rather than leaving it for rate-derivation, and explain in base_note",
      "base_note": "plain words on what this tax is actually calculated on, only if not the plain net subtotal"
    }
  ],
  "declared_subtotal": "as printed",
  "declared_total_tax": "as printed",
  "declared_grand_total": "the final amount the document says is owed",
  "notes": "anything unusual a human reviewer should know — e.g. a tax-inclusive price you'll convert, a discrepancy you noticed, or low confidence in a field"
}

Rules: every value must come from the page, "" if absent, never a guess. A
withholding tax reduces what's owed — its amount is NEGATIVE. Credit memos:
extract magnitudes as POSITIVE (invoice_type distinguishes it, not sign)."""


LINE_ITEMS_SYSTEM_PROMPT = """You are an expert accounts-payable clerk. You already
extracted this document's header fields separately. Now extract ONLY the line
items table, exactly as printed. Respond with JSON only, no prose, no markdown
fences. Keep each line item concise — no need to repeat header information."""

LINE_ITEMS_USER_PROMPT = """Extract every row of this document's line-items table as JSON:

{
  "line_items": [
    {
      "description": "",
      "item_type": "GOODS | SERVICE | FREIGHT",
      "uom": "",
      "quantity": "",
      "unit_price": "the NET (tax-exclusive) unit price — if the printed price is tax-inclusive, divide out the tax rate yourself",
      "line_total": "",
      "discount": "amount discount if shown on this line",
      "discount_percentage": "percentage discount if shown on this line",
      "tax_rate": "only if this line has its own tax rate separate from any header tax",
      "tax_amount": "only if this line has its own tax amount",
      "taxes": []
    }
  ]
}

Rules: include every line, even a zero-amount line (e.g. a free sample) — do not
drop it. Extract quantities/prices exactly as printed, do not round or smooth."""


def _list(result: dict, key: str) -> list:
    v = result.get(key, [])
    return v if isinstance(v, list) else []


def _str(result: dict, key: str, default: str = "") -> str:
    v = result.get(key, default)
    return v if isinstance(v, str) else default


def extract_header(pages_b64png: list[str], client: VisionClient) -> dict[str, Any]:
    """One small, fixed-size call for everything except line items."""
    result = client.extract_json(
        system_prompt=HEADER_SYSTEM_PROMPT,
        user_prompt=HEADER_USER_PROMPT,
        images_b64_png=pages_b64png,
        max_tokens=700,
    )
    return {
        "doc_type": _str(result, "doc_type", "invoice"),
        "invoice_number": _str(result, "invoice_number"),
        "invoice_date": _str(result, "invoice_date"),
        "due_date": _str(result, "due_date"),
        "currency": _str(result, "currency"),
        "supplier_name": _str(result, "supplier_name"),
        "supplier_vat_id": _str(result, "supplier_vat_id"),
        "supplier_address": _str(result, "supplier_address"),
        "supplier_country": _str(result, "supplier_country"),
        "buyer_name": _str(result, "buyer_name"),
        "payment_term_text": _str(result, "payment_term_text"),
        "po_number": _str(result, "po_number"),
        "header_charges": _list(result, "header_charges"),
        "header_taxes": _list(result, "header_taxes"),
        "declared_subtotal": _str(result, "declared_subtotal"),
        "declared_total_tax": _str(result, "declared_total_tax"),
        "declared_grand_total": _str(result, "declared_grand_total"),
        "notes": _str(result, "notes"),
    }


def extract_line_items(pages_b64png: list[str], client: VisionClient, *, max_tokens: int = 900) -> list[dict]:
    """A separate call, since this is the part whose size scales with the
    document (this kit has documents with up to 35 lines)."""
    result = client.extract_json(
        system_prompt=LINE_ITEMS_SYSTEM_PROMPT,
        user_prompt=LINE_ITEMS_USER_PROMPT,
        images_b64_png=pages_b64png,
        max_tokens=max_tokens,
    )
    return _list(result, "line_items")


def extract_payable(pages_b64png: list[str], client: VisionClient) -> dict[str, Any]:
    """Convenience wrapper: runs both calls and merges them into the same shape
    the old single-call version returned, so nothing downstream (autodraft.py,
    tests) needs to know this is now two API calls instead of one."""
    header = extract_header(pages_b64png, client)
    header["line_items"] = extract_line_items(pages_b64png, client)
    return header