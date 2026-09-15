import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookable_payable.classify import classify_batch
from bookable_payable.llm.base import VisionClient


class FakeVisionClient(VisionClient):
    def __init__(self, canned_responses: list[dict]):
        self.canned_responses = list(canned_responses)
        self.calls: list[dict] = []

    def extract_json(self, *, system_prompt, user_prompt, images_b64_png, max_tokens=900):
        self.calls.append({"user_prompt": user_prompt, "images": images_b64_png})
        return self.canned_responses.pop(0)


def test_single_payable_segment_passes_through():
    fake = FakeVisionClient([{
        "segments": [
            {"pages": [1, 2, 3], "is_payable": True, "new_payable": True,
             "invoice_number": "INV-1", "doc_type": "invoice", "reason": "clear invoice"},
        ]
    }])
    segments = classify_batch(["p1", "p2", "p3"], [1, 2, 3], fake)
    assert len(segments) == 1
    assert segments[0]["pages"] == [1, 2, 3]
    assert segments[0]["is_payable"] is True
    assert segments[0]["invoice_number"] == "INV-1"


def test_non_payable_segment():
    fake = FakeVisionClient([{
        "segments": [
            {"pages": [4], "is_payable": False, "new_payable": False,
             "invoice_number": "", "doc_type": "delivery_note", "reason": "packing slip"},
        ]
    }])
    segments = classify_batch(["p4"], [4], fake)
    assert segments[0]["is_payable"] is False
    assert segments[0]["doc_type"] == "delivery_note"


def test_page_the_model_never_mentions_is_not_silently_dropped():
    # model only addresses page 5, page 6 goes unmentioned
    fake = FakeVisionClient([{
        "segments": [
            {"pages": [5], "is_payable": True, "new_payable": True,
             "invoice_number": "X", "doc_type": "invoice", "reason": "invoice start"},
        ]
    }])
    segments = classify_batch(["p5", "p6"], [5, 6], fake)
    all_pages_covered = sorted(p for seg in segments for p in seg["pages"])
    assert all_pages_covered == [5, 6]
    missing_seg = next(s for s in segments if s["pages"] == [6])
    assert missing_seg["is_payable"] is False  # conservative default, not invented as payable


def test_page_claimed_twice_is_only_kept_once():
    # a buggy/hallucinated response claims page 7 in two different segments
    fake = FakeVisionClient([{
        "segments": [
            {"pages": [7], "is_payable": True, "new_payable": True, "invoice_number": "A", "doc_type": "invoice", "reason": "first claim"},
            {"pages": [7], "is_payable": False, "new_payable": False, "invoice_number": "", "doc_type": "other", "reason": "second claim"},
        ]
    }])
    segments = classify_batch(["p7"], [7], fake)
    all_pages = [p for seg in segments for p in seg["pages"]]
    assert all_pages == [7]  # page 7 appears exactly once total, not in both segments


def test_multiple_segments_in_one_batch():
    # a batch can hold the end of one thing and the start of another
    fake = FakeVisionClient([{
        "segments": [
            {"pages": [1], "is_payable": True, "new_payable": False, "invoice_number": "A", "doc_type": "invoice", "reason": "continuation"},
            {"pages": [2, 3], "is_payable": True, "new_payable": True, "invoice_number": "B", "doc_type": "invoice", "reason": "new invoice starts"},
        ]
    }])
    segments = classify_batch(["p1", "p2", "p3"], [1, 2, 3], fake)
    assert len(segments) == 2
    assert segments[0]["invoice_number"] == "A"
    assert segments[1]["invoice_number"] == "B"


def test_malformed_response_is_treated_as_no_evidence_not_a_crash():
    fake = FakeVisionClient([{"segments": "not a list"}])
    segments = classify_batch(["p1"], [1], fake)
    assert len(segments) == 1
    assert segments[0]["is_payable"] is False
    assert segments[0]["pages"] == [1]