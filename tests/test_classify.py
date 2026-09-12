import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookable_payable.classify import classify_document
from bookable_payable.llm.base import VisionClient


class FakeVisionClient(VisionClient):
    """Stands in for a real AI backend in tests: returns whatever canned answer
    we hand it, so we can test OUR parsing/validation logic in isolation."""

    def __init__(self, canned_response: dict):
        self.canned_response = canned_response
        self.last_call_kwargs: dict | None = None

    def extract_json(self, *, system_prompt, user_prompt, images_b64_png, max_tokens=4096):
        self.last_call_kwargs = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "images_b64_png": images_b64_png,
            "max_tokens": max_tokens,
        }
        return self.canned_response


def test_normal_invoice_passes_through():
    fake = FakeVisionClient({"doc_type": "invoice", "is_payable": True, "payable_count": 1, "reason": "It's an invoice"})
    result = classify_document(["fake_page_1"], fake)
    assert result == {"doc_type": "invoice", "is_payable": True, "payable_count": 1, "reason": "It's an invoice"}


def test_delivery_note_is_not_payable():
    fake = FakeVisionClient({"doc_type": "delivery_note", "is_payable": False, "payable_count": 0, "reason": "Delivery note, not a bill"})
    result = classify_document(["fake_page_1"], fake)
    assert result["is_payable"] is False
    assert result["payable_count"] == 0


def test_contradiction_payable_true_but_count_zero_is_fixed():
    # the model said payable but forgot to set a count — our code should not
    # silently produce a payable with 0 lines' worth of intent
    fake = FakeVisionClient({"doc_type": "invoice", "is_payable": True, "payable_count": 0, "reason": "invoice"})
    result = classify_document(["fake_page_1"], fake)
    assert result["payable_count"] == 1


def test_contradiction_not_payable_but_count_positive_is_fixed():
    fake = FakeVisionClient({"doc_type": "statement", "is_payable": False, "payable_count": 3, "reason": "just a statement"})
    result = classify_document(["fake_page_1"], fake)
    assert result["payable_count"] == 0


def test_missing_fields_do_not_crash():
    fake = FakeVisionClient({})  # a badly-behaved response
    result = classify_document(["fake_page_1"], fake)
    assert result["is_payable"] is False
    assert result["payable_count"] == 0
    assert result["doc_type"] == "other"


def test_pages_are_actually_passed_to_the_client():
    fake = FakeVisionClient({"doc_type": "invoice", "is_payable": True, "payable_count": 1, "reason": "x"})
    classify_document(["page_a", "page_b"], fake)
    assert fake.last_call_kwargs["images_b64_png"] == ["page_a", "page_b"]