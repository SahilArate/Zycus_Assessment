import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookable_payable.llm.base import VisionClient
from bookable_payable.masterdata import MasterData
from bookable_payable.pipeline import process_document

MD = MasterData.load(Path(__file__).resolve().parents[1] / "master_data")


class ScriptedClient(VisionClient):
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.prompts_seen: list[str] = []

    def extract_json(self, *, system_prompt, user_prompt, images_b64_png, max_tokens=900):
        self.prompts_seen.append(user_prompt)
        return self.responses.pop(0)


def _make_pdf(path: Path):
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()


WRONG_HEADER = {"invoice_number": "X", "currency": "USD", "declared_grand_total": "100.00", "header_charges": [], "header_taxes": []}
LINE_ITEMS_UNDERSHOOTING = {"line_items": [{"description": "A", "quantity": "1", "unit_price": "50.00", "line_total": "50.00"}]}


def test_unresolved_candidate_is_declined_not_emitted_as_payable(tmp_path):
    pdf_path = tmp_path / "mismatched.pdf"
    _make_pdf(pdf_path)

    client = ScriptedClient([
        {"doc_type": "invoice", "is_payable": True, "payable_count": 1, "reason": "invoice"},  # classify
        WRONG_HEADER, LINE_ITEMS_UNDERSHOOTING,  # attempt 1 (mismatch: booked 50 vs declared 100)
        WRONG_HEADER, LINE_ITEMS_UNDERSHOOTING,  # attempt 2 (still mismatch)
        WRONG_HEADER, LINE_ITEMS_UNDERSHOOTING,  # attempt 3 (still mismatch, retries exhausted)
    ])
    result = process_document(pdf_path, client, MD)

    assert result["payables"] == []  # NOT emitted despite being a real invoice
    assert len(result["declined"]) == 1
    assert result["declined"][0]["doc_type"] == "invoice"
    assert "could not reconcile" in result["declined"][0]["reason"].lower()


def test_multi_payable_candidates_get_distinguishing_hints(tmp_path):
    pdf_path = tmp_path / "two_invoices.pdf"
    _make_pdf(pdf_path)

    correct = {"invoice_number": "X", "currency": "USD", "declared_grand_total": "50.00", "header_charges": [], "header_taxes": []}
    lines = {"line_items": [{"description": "A", "quantity": "1", "unit_price": "50.00", "line_total": "50.00"}]}

    client = ScriptedClient([
        {"doc_type": "invoice", "is_payable": True, "payable_count": 2, "reason": "two stapled invoices"},  # classify
        correct, lines,  # candidate 1, resolves first try
        correct, lines,  # candidate 2, resolves first try
    ])
    process_document(pdf_path, client, MD)

    # candidate 2's extraction prompts should mention it's candidate 2 of 2 —
    # proof it's not blindly re-extracting the same invoice with no distinction
    header_prompt_for_candidate_2 = client.prompts_seen[3]  # index: 0=classify,1=c1-header,2=c1-lines,3=c2-header
    assert "candidate 2 of 2" in header_prompt_for_candidate_2