"""Turns raw extracted fields (whatever the vision step reads off a page) into a
payable that matches AUTODRAFT_SCHEMA.md exactly. This is pure data-shaping and
master-data resolution — no AI calls happen here, which is exactly why it's fully
testable without any API key.

Every numeric-looking field passes through _clean_num() before it enters the
payable. This matters more than it looks: erp.py's own number parser strips
currency symbols and "%" but NOT thousands-separator commas — feeding it
"7,200.00" would silently become 0.0 (float() fails, caught, defaults to zero),
not an error. Real Groq/vision output does print numbers with commas, so this
is a genuine bug class, not a hypothetical one, and it belongs here: this file
is the one boundary between "text as printed" and "numbers a calculator can use."
"""
from __future__ import annotations

from typing import Any

from .masterdata import MasterData

_WITHHOLDING_HINTS = ("withhold", "wht", "retention")


def _clean_num(v: Any) -> str:
    """Strip thousands-separator commas and stray whitespace from a numeric-ish
    string. Returns "" for empty input. Does NOT try to validate the result is a
    real number — erp.py's own parser already defaults unparseable input to 0.0,
    and we want that failure to be visible (a genuinely garbled value), not
    masked here. This function only removes formatting, never invents data."""
    if v is None:
        return ""
    s = str(v).strip()
    if s == "":
        return ""
    return s.replace(",", "")


def _fmt(x: float) -> str:
    return f"{x:.2f}"


def _sum_charge_amounts(charges: list[dict]) -> str:
    """Header-level charges (management fees, service fees, etc. that aren't
    freight/insurance/excise) collapse into extra_charges as one summed amount."""
    if not charges:
        return ""
    total = 0.0
    for c in charges:
        try:
            total += float(_clean_num(c.get("amount", "0")) or "0")
        except ValueError:
            continue
    return _fmt(total)


def _guess_tax_type(label: str, amount_str: str) -> str:
    label_l = (label or "").lower()
    try:
        amt = float(_clean_num(amount_str) or "0")
    except ValueError:
        amt = 0.0
    if amt < 0 or any(h in label_l for h in _WITHHOLDING_HINTS):
        return "WITHHOLDING"
    if "vat" in label_l:
        return "VAT"
    if "gst" in label_l:
        return "GST"
    return (label or "").strip().upper()[:20] or "OTHER"


def _map_tax(t: dict, master: MasterData, country: str = "") -> dict:
    label = t.get("label", "")
    rate = _clean_num(str(t.get("rate_percent", "") or "").replace("%", "").strip())
    amount = _clean_num(t.get("amount", ""))
    tax_type = _guess_tax_type(label, amount)
    return {
        "tax_type": tax_type,
        "tax_name": label,
        "tax_rate": rate,
        "tax_amount": amount,
        "tax_type_code": master.resolve_tax(country=country, tax_type=tax_type, rate_percent=rate) if rate else "",
    }


def _map_line(li: dict, master: MasterData, country: str = "") -> dict:
    taxes = li.get("taxes") or []
    return {
        "description": li.get("description", ""),
        "item_type": li.get("item_type", "GOODS"),
        "uom": li.get("uom", ""),
        "quantity": _clean_num(li.get("quantity", "")),
        "unit_price": _clean_num(li.get("unit_price", "")),
        "total": _clean_num(li.get("line_total", li.get("total", ""))),
        "discount": _clean_num(li.get("discount", "")),
        "discount_percentage": _clean_num(li.get("discount_percentage", "")),
        "tax_rate": _clean_num(li.get("tax_rate", "")) if not taxes else "",
        "tax_amount": _clean_num(li.get("tax_amount", "")) if not taxes else "",
        "taxes": [_map_tax(t, master, country) for t in taxes],
    }


def build_payable(raw: dict, master: MasterData, *, company_code: str, business_unit_code: str, country: str = "") -> dict:
    """raw: the vision-extraction output for one payable candidate (see extraction
    prompt for its shape). country: ISO-ish country hint for tax-master resolution,
    e.g. derived from the supplier's address/VAT prefix — plain best-effort, and
    resolve_tax already tolerates "" (matches on type+rate only)."""
    supplier_id, _ = master.resolve_supplier(
        name=raw.get("supplier_name", ""),
        vat_id=raw.get("supplier_vat_id", ""),
    )
    buyer = master.resolve_buyer(company_code, business_unit_code)

    return {
        "invoice_number": raw.get("invoice_number", ""),
        "invoice_date": raw.get("invoice_date", ""),
        "due_date": raw.get("due_date", ""),
        "invoice_type": "CREDIT_MEMO" if raw.get("doc_type") == "credit_memo" else "INVOICE",
        "currency": raw.get("currency", ""),
        "supplier": {
            "name": raw.get("supplier_name", ""),
            "supplier_id": supplier_id,
            "address": raw.get("supplier_address", ""),
            "vat_id": raw.get("supplier_vat_id", ""),
        },
        "buyer": buyer,
        "payment_term_id": master.resolve_payment_term(raw.get("payment_term_text", "")),
        "po_number": raw.get("po_number", ""),
        "po_id": master.resolve_po(raw.get("po_number", "")),
        "gross_total": _clean_num(raw.get("declared_grand_total", "")),
        "subtotal": _clean_num(raw.get("declared_subtotal", "")),
        "total_tax_amount": _clean_num(raw.get("declared_total_tax", "")),
        "discount_amount": _clean_num(raw.get("header_discount_amount", "")),
        "freight_charges": _clean_num(raw.get("freight_charges", "")),
        "insurance_charges": _clean_num(raw.get("insurance_charges", "")),
        "extra_charges": _sum_charge_amounts(raw.get("header_charges", [])),
        "excise_duties": _clean_num(raw.get("excise_duties", "")),
        "taxes": [_map_tax(t, master, country) for t in raw.get("header_taxes", [])],
        "line_items": [_map_line(li, master, country) for li in raw.get("line_items", [])],
    }