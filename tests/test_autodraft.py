import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookable_payable.autodraft import build_payable
from bookable_payable.masterdata import MasterData
from bookable_payable.verify import verify_payable

MD = MasterData.load(Path(__file__).resolve().parents[1] / "master_data")

# Raw fields as if extracted from HLD-01.pdf — the Thai invoice with a 9% management
# fee, VAT applied on (subtotal + fee) rather than the plain net, and a 3% withholding
# tax. Values match what we hand-verified earlier: this document's real total payment
# is 8161.92 THB.
HLD01_RAW = {
    "doc_type": "invoice",
    "invoice_number": "SI6675/02/467",
    "invoice_date": "2026-05-05",
    "currency": "THB",
    "supplier_name": "Sinc Creation Co., Ltd.",
    "supplier_vat_id": "",
    "line_items": [
        {"description": "Staff 2 Units X 6 Days", "quantity": "12", "unit_price": "600.00", "line_total": "7200.00"}
    ],
    "header_charges": [
        {"label": "Management Fee", "basis": "9% of subtotal", "amount": "648.00"}
    ],
    "header_taxes": [
        {"label": "VAT", "rate_percent": "7", "amount": "549.36", "base_note": "on subtotal + management fee"},
        {"label": "Withholding Tax", "rate_percent": "3", "amount": "-235.44", "base_note": "reduces payment"},
    ],
    "declared_subtotal": "7200.00",
    "declared_total_tax": "313.92",
    "declared_grand_total": "8161.92",
}


def test_hld01_builds_and_matches_oracle():
    payable = build_payable(HLD01_RAW, MD)
    result = verify_payable(payable)
    assert result["matches"], result
    assert result["booked_gross"] == 8161.92


def test_hld01_extra_charges_captured_as_header_charge():
    payable = build_payable(HLD01_RAW, MD)
    assert payable["extra_charges"] == "648.00"


def test_hld01_withholding_tax_kept_negative():
    payable = build_payable(HLD01_RAW, MD)
    wht = [t for t in payable["taxes"] if t["tax_type"] == "WITHHOLDING"]
    assert len(wht) == 1
    assert wht[0]["tax_amount"] == "-235.44"


def test_hld01_unknown_supplier_is_honest_blank():
    payable = build_payable(HLD01_RAW, MD)
    assert payable["supplier"]["supplier_id"] == ""  # not in our small master list — must not guess


def test_mismatch_is_detected_not_silently_passed():
    payable = build_payable(HLD01_RAW, MD)
    payable["gross_total"] = "9999.99"  # deliberately wrong
    result = verify_payable(payable)
    assert not result["matches"]
    assert result["diff"] != 0


# The actual raw output Groq/qwen returned for HLD-01.pdf in a real run — numbers
# printed with thousands-separator commas, exactly as a real vision model does.
# This is the regression test for a real bug: erp.py's own number parser does not
# strip commas, so "7,200.00" would silently become 0.0 without _clean_num().
HLD01_REAL_GROQ_OUTPUT = {
    "doc_type": "invoice",
    "invoice_number": "SI6675/02/467",
    "invoice_date": "2026-05-05",
    "due_date": "",
    "currency": "THB",
    "supplier_name": "บริษัท ซิงค์ ครีเอชั่น จำกัด",
    "supplier_vat_id": "0 1055 56100 87 9",
    "supplier_address": "เลขที่ 799/124 หมู่ที่ 3 ตำบลเพชรเกษม แขวงประเวศ เขตประเวศ กทม 10250",
    "supplier_country": "TH",
    "buyer_name": "Northwind SUPPORT SERVICES (THAILAND) LIMITED",
    "payment_term_text": "",
    "po_number": "",
    "header_charges": [
        {"label": "MANAGEMENT FEE 9%", "basis": "9% of net subtotal", "amount": "648.00"}
    ],
    "header_taxes": [
        {"label": "Vat 7%", "rate_percent": "7%", "amount": "549.36", "base_note": "on total including agency fee (7,848.00)"},
        {"label": "WITHHOLDING TAX", "rate_percent": "3%", "amount": "-235.44", "base_note": "3% of gross total including VAT"},
    ],
    "declared_subtotal": "7,200.00",  # <-- comma
    "declared_total_tax": "",
    "declared_grand_total": "8,161.92",  # <-- comma
    "notes": "Date converted from Thai Buddhist Era 2569.",
    "line_items": [
        {
            "description": "Staff 2 Units X 6 Days", "item_type": "SERVICE", "uom": "Units",
            "quantity": "12", "unit_price": "600.00", "line_total": "7,200.00",  # <-- comma
            "discount": "", "discount_percentage": "", "tax_rate": "", "tax_amount": "", "taxes": [],
        }
    ],
}


def test_real_groq_output_with_commas_still_matches_oracle():
    payable = build_payable(HLD01_REAL_GROQ_OUTPUT, MD)
    result = verify_payable(payable)
    assert result["matches"], result
    assert result["booked_gross"] == 8161.92


def test_real_groq_output_line_total_is_not_silently_zeroed():
    # the exact failure mode this fix prevents: a comma-formatted line total
    # must not collapse to 0 once it reaches the payable
    payable = build_payable(HLD01_REAL_GROQ_OUTPUT, MD)
    assert payable["line_items"][0]["total"] == "7200.00"
    assert payable["gross_total"] == "8161.92"