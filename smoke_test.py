"""One-off smoke test — not part of the final pipeline. Proves the extraction
call works end to end on a real, tricky document before we build the rest."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from bookable_payable.ingestion import render_pdf_pages
from bookable_payable.llm import get_vision_client

SYSTEM = """You are an expert accounts-payable document reader. You read one page
image of a supplier document and extract every raw component exactly as printed —
never compute or invent a number that is not on the page. Respond with JSON only,
no prose, no markdown fences."""

USER = """Extract this document's raw fields as JSON with this shape:
{
  "doc_type": "invoice | credit_memo | delivery_note | statement | other",
  "invoice_number": "", "invoice_date": "", "due_date": "",
  "currency": "",
  "supplier_name": "", "supplier_vat_id": "", "supplier_address": "",
  "buyer_name": "", "buyer_address": "",
  "payment_term_text": "",
  "po_number": "",
  "line_items": [
    {"description": "", "quantity": "", "unit_price": "", "line_total": ""}
  ],
  "header_charges": [
    {"label": "", "basis": "", "amount": ""}
  ],
  "header_taxes": [
    {"label": "", "rate_percent": "", "amount": "", "base_note": ""}
  ],
  "declared_subtotal": "", "declared_total_tax": "", "declared_grand_total": ""
}
Every number must appear on the page. If a field is absent, use "". If a charge or
tax is computed on a base other than the plain line subtotal (e.g. a fee-on-fee,
or VAT applied after another charge), note that in "basis"/"base_note" in plain words."""

pages = render_pdf_pages("documents/HLD-01.pdf")
print(f"rendered {len(pages)} page(s)")

client = get_vision_client()
result = client.extract_json(system_prompt=SYSTEM, user_prompt=USER, images_b64_png=pages)
print(json.dumps(result, indent=2, ensure_ascii=False))
