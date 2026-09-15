"""Tests _merge_segments_into_payables directly — the core of the batching
rewrite — without needing real PDFs or a vision client. Segment shape matches
exactly what classify_batch() returns."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookable_payable.pipeline import _merge_segments_into_payables


def _seg(pages, is_payable=True, new_payable=False, invoice_number="", doc_type="invoice", reason=""):
    return {"pages": pages, "is_payable": is_payable, "new_payable": new_payable,
            "invoice_number": invoice_number, "doc_type": doc_type, "reason": reason}


def test_invoice_spanning_two_batches_becomes_one_payable_with_all_its_pages():
    segments = [
        _seg([1, 2, 3], new_payable=True, invoice_number="INV-1"),
        _seg([4, 5, 6], new_payable=False, invoice_number="INV-1"),  # continuation, same invoice number
    ]
    payables, declined = _merge_segments_into_payables(segments)
    assert len(payables) == 1
    assert payables[0]["pages"] == [1, 2, 3, 4, 5, 6]


def test_continuation_without_invoice_number_attaches_to_open_payable():
    segments = [
        _seg([1, 2], new_payable=True, invoice_number="INV-1"),
        _seg([3], new_payable=False, invoice_number=""),  # no number visible on this page
    ]
    payables, declined = _merge_segments_into_payables(segments)
    assert len(payables) == 1
    assert payables[0]["pages"] == [1, 2, 3]


def test_two_distinct_invoices_with_supporting_pages_between_them():
    segments = [
        _seg([1, 2], new_payable=True, invoice_number="A"),
        _seg([3, 4, 5], is_payable=False, doc_type="delivery_note", reason="delivery note"),
        _seg([6, 7], new_payable=True, invoice_number="B"),
    ]
    payables, declined = _merge_segments_into_payables(segments)
    assert len(payables) == 2
    assert payables[0]["pages"] == [1, 2]
    assert payables[0]["invoice_number"] == "A"
    assert payables[1]["pages"] == [6, 7]
    assert payables[1]["invoice_number"] == "B"
    assert len(declined) == 1
    assert declined[0]["doc_type"] == "delivery_note"


def test_invoice_with_supporting_document_mixed_in_stays_one_payable():
    segments = [
        _seg([1], new_payable=True, invoice_number="X"),
        _seg([2], is_payable=False, doc_type="delivery_note"),
    ]
    payables, declined = _merge_segments_into_payables(segments)
    assert len(payables) == 1
    assert payables[0]["pages"] == [1]  # the delivery-note page never joins the payable's own pages


def test_no_payable_evidence_anywhere_yields_empty_payables():
    segments = [
        _seg([1], is_payable=False, doc_type="delivery_note"),
        _seg([2], is_payable=False, doc_type="statement"),
    ]
    payables, declined = _merge_segments_into_payables(segments)
    assert payables == []
    assert len(declined) == 2


def test_same_invoice_evidence_repeated_across_batches_does_not_duplicate():
    # model mistakenly re-flags new_payable=True for an invoice number it already saw
    segments = [
        _seg([1, 2], new_payable=True, invoice_number="DUP-1"),
        _seg([3], new_payable=True, invoice_number="DUP-1"),  # should merge, not duplicate
    ]
    payables, declined = _merge_segments_into_payables(segments)
    assert len(payables) == 1
    assert payables[0]["pages"] == [1, 2, 3]


def test_payable_count_is_never_a_naive_sum_across_batches():
    # 3 batches all touching the SAME invoice must still total to 1 payable, not 3
    segments = [
        _seg([1], new_payable=True, invoice_number="ONE"),
        _seg([2], new_payable=False, invoice_number="ONE"),
        _seg([3], new_payable=False, invoice_number="ONE"),
    ]
    payables, declined = _merge_segments_into_payables(segments)
    assert len(payables) == 1


def test_continuation_as_the_very_first_segment_is_not_silently_dropped():
    # defensive edge case: nothing open yet, but the model says "continuation"
    segments = [_seg([1], new_payable=False, invoice_number="")]
    payables, declined = _merge_segments_into_payables(segments)
    assert len(payables) == 1  # treated as a new payable rather than losing the page
    assert payables[0]["pages"] == [1]