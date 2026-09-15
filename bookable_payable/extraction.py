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
  "buyer_address": "",
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
  "freight_charges": "freight/shipping/delivery charge amount, only if printed as its own separate amount, else empty",
  "insurance_charges": "insurance charge amount, only if printed as its own separate amount, else empty",
  "excise_duties": "excise duty amount, only if printed as its own separate amount, else empty",
  "declared_subtotal": "as printed",
  "declared_total_tax": "as printed",
  "declared_grand_total": "the final amount the document itself says is owed (e.g. an 'AMOUNT DUE' figure, if that's the document's bottom line — not necessarily its pre-adjustment TOTAL)",
  "header_unrepresentable_amounts": [
    {
      "label": "the exact printed label, e.g. 'Less Amount Credited', 'Less Payments Received', 'Applied Credit'",
      "amount": "the printed amount",
      "reason": "one short sentence: why this is NOT a pricing discount, NOT a tax, and NOT freight/insurance/excise — e.g. it's a credit or prior payment being applied, not a price reduction"
    }
  ],
  "notes": "anything unusual a human reviewer should know — e.g. a tax-inclusive price you'll convert, a discrepancy you noticed, or low confidence in a field"
}

Rules: every value must come from the page, "" if absent, never a guess. A
withholding tax reduces what's owed — its amount is NEGATIVE. Credit memos:
extract magnitudes as POSITIVE (invoice_type distinguishes it, not sign).
Every NUMBER you output must be dot-decimal (e.g. 1796.54) regardless of how
it's punctuated on the page — if the document prints 1.796,54 or 1 796,54,
you are transcribing its VALUE as 1796.54, not its punctuation style. Never
add a thousands separator of your own.

If the document shows a reduction, credit, prior payment, or adjustment
applied against the total that is NOT a pricing discount, NOT a tax, and NOT
one of the charges above — for example a "Less Amount Credited" line reducing
a printed TOTAL down to a smaller AMOUNT DUE — do not force it into
discount_amount and do not invent a charge for it; this schema has no field
for a credit-already-applied. Report it honestly in
header_unrepresentable_amounts instead. Leave this list empty for ordinary
discounts, taxes, and charges — it's only for reductions that genuinely have
no home in this schema."""


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
      "unit_price": "the unit price's VALUE as printed, in dot-decimal notation (e.g. 14.76, not 14,76 or 1.234,56) — do not compute, convert, or divide the value itself, only transcribe it in dot-decimal form",
      "price_is_tax_inclusive": "true or false — true only if the document itself states or clearly shows this printed price already includes tax",
      "tax_inclusive_rate_percent": "only if price_is_tax_inclusive is true: the tax rate percent baked into that price, as printed or stated elsewhere on the document; else empty",
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
drop it. Extract quantities/prices exactly as printed (never round, smooth, or
compute a value that isn't itself printed), but always WRITE every number in
dot-decimal notation regardless of how it's punctuated on the page — you are
transcribing the value, not the page's punctuation style, and never add a
thousands separator of your own. If a price includes tax, report it as printed
and flag it with price_is_tax_inclusive and tax_inclusive_rate_percent; the net price is worked out
afterward in code, not by you."""


def _list(result: dict, key: str) -> list:
    v = result.get(key, [])
    return v if isinstance(v, list) else []


def _str(result: dict, key: str, default: str = "") -> str:
    v = result.get(key, default)
    return v if isinstance(v, str) else default

def extract_header(pages_b64png: list[str], client: VisionClient, *, feedback: str = "") -> dict[str, Any]:
    """One small, fixed-size call for everything except line items.
    feedback: if this is a retry after a failed erp.py check, a short note
    describing the discrepancy (size only, never a suggested fix — see
    pipeline._build_feedback) to prompt a genuine re-read, not a forced match."""
    user_prompt = HEADER_USER_PROMPT
    if feedback:
        user_prompt += f"\n\nIMPORTANT — this is a re-read after a discrepancy was found: {feedback}"
    result = client.extract_json(
        system_prompt=HEADER_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        images_b64_png=pages_b64png,
        max_tokens=850,
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
        "buyer_address": _str(result, "buyer_address"),
        "payment_term_text": _str(result, "payment_term_text"),
        "po_number": _str(result, "po_number"),
        "header_charges": _list(result, "header_charges"),
        "header_taxes": _list(result, "header_taxes"),
        "freight_charges": _str(result, "freight_charges"),
        "insurance_charges": _str(result, "insurance_charges"),
        "excise_duties": _str(result, "excise_duties"),
        "declared_subtotal": _str(result, "declared_subtotal"),
        "declared_total_tax": _str(result, "declared_total_tax"),
        "declared_grand_total": _str(result, "declared_grand_total"),
        "header_unrepresentable_amounts": _list(result, "header_unrepresentable_amounts"),
        "notes": _str(result, "notes"),
    }

def extract_line_items(pages_b64png: list[str], client: VisionClient, *, max_tokens: int = 900, feedback: str = "") -> list[dict]:
    """A separate call, since this is the part whose size scales with the
    document (this kit has documents with up to 35 lines)."""
    user_prompt = LINE_ITEMS_USER_PROMPT
    if feedback:
        user_prompt += f"\n\nIMPORTANT — this is a re-read after a discrepancy was found: {feedback}"
    result = client.extract_json(
        system_prompt=LINE_ITEMS_SYSTEM_PROMPT,
        user_prompt=user_prompt,
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