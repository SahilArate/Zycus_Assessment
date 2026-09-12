"""Turns raw extracted fields (whatever the vision step reads off a page) into a
payable that matches AUTODRAFT_SCHEMA.md exactly. This is pure data-shaping and
master-data resolution — no AI calls happen here, which is exactly why it's fully
testable without any API key.
"""
from __future__ import annotations

from typing import Any

from .masterdata import MasterData

_WITHHOLDING_HINTS = ("withhold", "wht", "retention")


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
            total += float(str(c.get("amount", "0") or "0").replace(",", ""))
        except ValueError:
            continue
    return _fmt(total)


def _guess_tax_type(label: str, amount_str: str) -> str:
    label_l = (label or "").lower()
    try:
        amt = float(str(amount_str or "0").replace(",", ""))
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
    rate = str(t.get("rate_percent", "") or "").replace("%", "").strip()
    amount = t.get("amount", "")
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
        "quantity": li.get("quantity", ""),
        "unit_price": li.get("unit_price", ""),
        "total": li.get("line_total", li.get("total", "")),
        "discount": li.get("discount", ""),
        "discount_percentage": li.get("discount_percentage", ""),
        "tax_rate": li.get("tax_rate", "") if not taxes else "",
        "tax_amount": li.get("tax_amount", "") if not taxes else "",
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
        "gross_total": raw.get("declared_grand_total", ""),
        "subtotal": raw.get("declared_subtotal", ""),
        "total_tax_amount": raw.get("declared_total_tax", ""),
        "discount_amount": raw.get("header_discount_amount", ""),
        "freight_charges": raw.get("freight_charges", ""),
        "insurance_charges": raw.get("insurance_charges", ""),
        "extra_charges": _sum_charge_amounts(raw.get("header_charges", [])),
        "excise_duties": raw.get("excise_duties", ""),
        "taxes": [_map_tax(t, master, country) for t in raw.get("header_taxes", [])],
        "line_items": [_map_line(li, master, country) for li in raw.get("line_items", [])],
    }