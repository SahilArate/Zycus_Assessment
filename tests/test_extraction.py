import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookable_payable.extraction import extract_header, extract_line_items, extract_payable
from bookable_payable.llm.base import VisionClient


class FakeVisionClient(VisionClient):
    """Returns canned responses in order, one per call — needed here because
    extract_payable() now makes two calls (header, then line items) and each
    needs its own fake answer."""

    def __init__(self, canned_responses: list[dict]):
        self.canned_responses = list(canned_responses)
        self.calls: list[dict] = []

    def extract_json(self, *, system_prompt, user_prompt, images_b64_png, max_tokens=900):
        self.calls.append({"system_prompt": system_prompt, "max_tokens": max_tokens})
        return self.canned_responses.pop(0)


def test_header_extraction_passes_through():
    fake = FakeVisionClient([{
        "doc_type": "invoice",
        "invoice_number": "12345",
        "currency": "EUR",
        "supplier_name": "Acme GmbH",
        "declared_grand_total": "20.00",
    }])
    result = extract_header(["page1"], fake)
    assert result["invoice_number"] == "12345"
    assert result["currency"] == "EUR"
    assert result["declared_grand_total"] == "20.00"
    assert "line_items" not in result  # header call never returns line items


def test_header_missing_keys_default_safely():
    fake = FakeVisionClient([{"invoice_number": "999"}])
    result = extract_header(["page1"], fake)
    assert result["header_charges"] == []
    assert result["header_taxes"] == []
    assert result["supplier_name"] == ""
    assert result["doc_type"] == "invoice"  # sensible default, never crashes


def test_line_items_extraction_passes_through():
    fake = FakeVisionClient([{
        "line_items": [{"description": "Widget", "quantity": "2", "unit_price": "10.00", "line_total": "20.00"}]
    }])
    result = extract_line_items(["page1"], fake)
    assert len(result) == 1
    assert result[0]["description"] == "Widget"


def test_line_items_wrong_type_does_not_crash():
    fake = FakeVisionClient([{"line_items": "oops not a list"}])
    result = extract_line_items(["page1"], fake)
    assert result == []


def test_extract_payable_merges_both_calls():
    fake = FakeVisionClient([
        {"invoice_number": "555", "currency": "USD", "declared_grand_total": "100.00"},  # header call
        {"line_items": [{"description": "Service A", "quantity": "1", "unit_price": "100.00", "line_total": "100.00"}]},  # line items call
    ])
    result = extract_payable(["page1"], fake)
    assert result["invoice_number"] == "555"
    assert len(result["line_items"]) == 1
    assert result["line_items"][0]["description"] == "Service A"
    assert len(fake.calls) == 2  # confirms it really made two separate, smaller calls


def test_header_freight_insurance_excise_pass_through():
    fake = FakeVisionClient([{
        "freight_charges": "25.00",
        "insurance_charges": "12.50",
        "excise_duties": "3.00",
    }])
    result = extract_header(["page1"], fake)
    assert result["freight_charges"] == "25.00"
    assert result["insurance_charges"] == "12.50"
    assert result["excise_duties"] == "3.00"


def test_header_freight_insurance_excise_default_blank():
    fake = FakeVisionClient([{"invoice_number": "1"}])
    result = extract_header(["page1"], fake)
    assert result["freight_charges"] == ""
    assert result["insurance_charges"] == ""
    assert result["excise_duties"] == ""


def test_notes_field_survives_for_human_review():
    fake = FakeVisionClient([{"notes": "Converted tax-inclusive unit price to net using 7% VAT rate"}])
    result = extract_header(["page1"], fake)
    assert "tax-inclusive" in result["notes"]