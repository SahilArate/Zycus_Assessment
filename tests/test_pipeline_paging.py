import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookable_payable.llm.base import VisionClient
from bookable_payable.masterdata import MasterData
from bookable_payable.pipeline import process_document

MD = MasterData.load(Path(__file__).resolve().parents[1] / "master_data")


class CountingVisionClient(VisionClient):
    """Only cares how many page images it was actually handed — proves the real
    render -> select_pages_for_model -> classify chain caps pages end to end,
    not just the helper function in isolation."""

    def __init__(self):
        self.image_counts_seen: list[int] = []

    def extract_json(self, *, system_prompt, user_prompt, images_b64_png, max_tokens=900):
        self.image_counts_seen.append(len(images_b64_png))
        return {"doc_type": "delivery_note", "is_payable": False, "payable_count": 0, "reason": "test document"}


def _make_pdf(path: Path, n_pages: int):
    doc = pymupdf.open()
    for _ in range(n_pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


def test_long_document_never_sends_more_than_the_model_cap(tmp_path):
    pdf_path = tmp_path / "long_delivery_note.pdf"
    _make_pdf(pdf_path, n_pages=15)  # like the real DU-05s in the kit

    client = CountingVisionClient()
    result = process_document(pdf_path, client, MD)

    assert client.image_counts_seen == [3]  # classification call, capped to the real Groq model limit
    assert result["file"] == "long_delivery_note.pdf"
    assert result["declined"][0]["doc_type"] == "delivery_note"


def test_short_document_sends_all_its_pages(tmp_path):
    pdf_path = tmp_path / "short_invoice.pdf"
    _make_pdf(pdf_path, n_pages=2)

    client = CountingVisionClient()
    process_document(pdf_path, client, MD)

    assert client.image_counts_seen == [2]  # under the cap, nothing trimmed