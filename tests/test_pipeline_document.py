import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookable_payable.llm.base import VisionClient
from bookable_payable.masterdata import MasterData
from bookable_payable.pipeline import process_document

MD = MasterData.load(Path(__file__).resolve().parents[1] / "master_data")


class ScriptedClient(VisionClient):
    def __init__(self, responses: list):
        self.responses = list(responses)
        self.prompts_seen: list[str] = []

    def extract_json(self, *, system_prompt, user_prompt, images_b64_png, max_tokens=900):
        self.prompts_seen.append(user_prompt)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _make_pdf(path: Path, n_pages: int = 1):
    doc = pymupdf.open()
    for _ in range(n_pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


def _classify_response(segments: list[dict]) -> dict:
    return {"segments": segments}


WRONG_HEADER = {"invoice_number": "X", "currency": "USD", "declared_grand_total": "100.00", "header_charges": [], "header_taxes": []}
LINE_ITEMS_UNDERSHOOTING = {"line_items": [{"description": "A", "quantity": "1", "unit_price": "50.00", "line_total": "50.00"}]}

CORRECT_HEADER_50 = {"invoice_number": "X", "currency": "USD", "declared_grand_total": "50.00", "header_charges": [], "header_taxes": []}
LINE_ITEMS_50 = {"line_items": [{"description": "A", "quantity": "1", "unit_price": "50.00", "line_total": "50.00"}]}


def test_unresolved_candidate_is_declined_not_emitted_as_payable(tmp_path):
    pdf_path = tmp_path / "mismatched.pdf"
    _make_pdf(pdf_path, n_pages=1)

    client = ScriptedClient([
        _classify_response([{"pages": [1], "is_payable": True, "new_payable": True,
                              "invoice_number": "X", "doc_type": "invoice", "reason": "invoice"}]),
        WRONG_HEADER, LINE_ITEMS_UNDERSHOOTING,  # attempt 1 (mismatch: booked 50 vs declared 100)
        WRONG_HEADER, LINE_ITEMS_UNDERSHOOTING,  # attempt 2 (still mismatch)
        WRONG_HEADER, LINE_ITEMS_UNDERSHOOTING,  # attempt 3 (still mismatch, retries exhausted)
    ])
    result = process_document(pdf_path, client, MD)

    assert result["payables"] == []  # NOT emitted despite being a real invoice
    assert len(result["declined"]) == 1
    assert result["declined"][0]["doc_type"] == "invoice"
    assert "could not reconcile" in result["declined"][0]["reason"].lower()


def test_long_document_payable_found_only_in_a_later_batch(tmp_path):
    # 6 pages -> two batches of 3. Batch 1 is entirely supporting pages, the
    # real invoice only appears in batch 2 — this is exactly the DU-02/DU-03
    # style case the old 3-page-total cap would have missed.
    pdf_path = tmp_path / "long_doc.pdf"
    _make_pdf(pdf_path, n_pages=6)

    client = ScriptedClient([
        _classify_response([{"pages": [1, 2, 3], "is_payable": False, "new_payable": False,
                              "invoice_number": "", "doc_type": "delivery_note", "reason": "supporting pages"}]),
        _classify_response([{"pages": [4, 5, 6], "is_payable": True, "new_payable": True,
                              "invoice_number": "X", "doc_type": "invoice", "reason": "invoice found here"}]),
        CORRECT_HEADER_50, LINE_ITEMS_50,  # resolves on first attempt
    ])
    result = process_document(pdf_path, client, MD)

    assert len(result["payables"]) == 1
    assert result["payables"][0]["gross_total"] == "50.00"
    assert result["declined"] == []


def test_invoice_spanning_two_batches_produces_one_payable_not_two(tmp_path):
    pdf_path = tmp_path / "spanning.pdf"
    _make_pdf(pdf_path, n_pages=6)

    client = ScriptedClient([
        _classify_response([{"pages": [1, 2, 3], "is_payable": True, "new_payable": True,
                              "invoice_number": "X", "doc_type": "invoice", "reason": "invoice starts"}]),
        _classify_response([{"pages": [4, 5, 6], "is_payable": True, "new_payable": False,
                              "invoice_number": "X", "doc_type": "invoice", "reason": "continuation of X"}]),
        CORRECT_HEADER_50, LINE_ITEMS_50,
    ])
    result = process_document(pdf_path, client, MD)

    assert len(result["payables"]) == 1  # not two, despite two batches both reporting payable evidence


def test_no_payable_anywhere_in_the_document(tmp_path):
    pdf_path = tmp_path / "not_a_payable.pdf"
    _make_pdf(pdf_path, n_pages=1)

    client = ScriptedClient([
        _classify_response([{"pages": [1], "is_payable": False, "new_payable": False,
                              "invoice_number": "", "doc_type": "statement", "reason": "account statement, not a bill"}]),
    ])
    result = process_document(pdf_path, client, MD)

    assert result["payables"] == []
    assert len(result["declined"]) == 1
    assert result["declined"][0]["doc_type"] == "statement"
    assert "account statement" in result["declined"][0]["reason"].lower()


def test_batch_classification_failure_propagates_not_silently_becomes_not_payable(tmp_path):
    # a genuine crash in classifying one batch must surface as a real error
    # (main.py's per-document handler is what writes an honest error entry) —
    # it must NOT be swallowed here and reported as "this document isn't a payable".
    pdf_path = tmp_path / "six_pages.pdf"
    _make_pdf(pdf_path, n_pages=6)

    client = ScriptedClient([
        _classify_response([{"pages": [1, 2, 3], "is_payable": False, "new_payable": False,
                              "invoice_number": "", "doc_type": "delivery_note", "reason": "ok"}]),
        RuntimeError("vision API exploded on batch 2"),
    ])
    try:
        process_document(pdf_path, client, MD)
        assert False, "expected the batch failure to propagate, not be swallowed"
    except RuntimeError as e:
        assert "batch 2" in str(e)