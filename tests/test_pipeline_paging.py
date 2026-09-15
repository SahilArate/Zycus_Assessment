import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookable_payable.llm.base import VisionClient
from bookable_payable.masterdata import MasterData
from bookable_payable.pipeline import process_document

MD = MasterData.load(Path(__file__).resolve().parents[1] / "master_data")


class CountingVisionClient(VisionClient):
    """Counts how many images EACH call gets, and how many TOTAL page-slots
    were seen across all calls combined — proves both halves of the fix: no
    single call ever exceeds the real model cap, AND every page of a long
    document is still seen by SOME call (the old bug capped the whole
    document to 3 pages total; this proves that's gone)."""

    def __init__(self):
        self.image_counts_seen: list[int] = []

    def extract_json(self, *, system_prompt, user_prompt, images_b64_png, max_tokens=900):
        self.image_counts_seen.append(len(images_b64_png))
        return {"segments": [{
            "pages": list(range(1, len(images_b64_png) + 1)),  # placeholder, overridden per-test where it matters
            "is_payable": False, "new_payable": False, "invoice_number": "", "doc_type": "delivery_note", "reason": "test document",
        }]}


def _make_pdf(path: Path, n_pages: int):
    doc = pymupdf.open()
    for _ in range(n_pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


def test_long_document_no_single_call_exceeds_the_model_cap(tmp_path):
    pdf_path = tmp_path / "long_delivery_note.pdf"
    _make_pdf(pdf_path, n_pages=15)  # like the real DU-05s in the kit

    client = CountingVisionClient()
    result = process_document(pdf_path, client, MD)

    assert all(n <= 3 for n in client.image_counts_seen)  # the real, hard per-request limit
    assert result["file"] == "long_delivery_note.pdf"


def test_moderately_long_document_still_gets_full_page_coverage(tmp_path):
    # at/under FULL_BATCH_PAGE_THRESHOLD (6), every page is still seen —
    # this is the common case (most documents in the kit are 1-2 pages) and
    # costs almost nothing extra to batch fully.
    pdf_path = tmp_path / "medium_document.pdf"
    _make_pdf(pdf_path, n_pages=6)

    client = CountingVisionClient()
    process_document(pdf_path, client, MD)

    assert sum(client.image_counts_seen) == 6  # every page reached the model exactly once


def test_very_long_document_falls_back_to_capped_sampling(tmp_path):
    # past the threshold, full batching would multiply cost too far for the
    # free-tier daily budget (confirmed by direct observation, not a guess —
    # see pipeline.py's module docstring point 4). A 15-page document like
    # the real DU-05s falls back to ONE capped sample instead of 5 batches.
    pdf_path = tmp_path / "long_delivery_note.pdf"
    _make_pdf(pdf_path, n_pages=15)

    client = CountingVisionClient()
    process_document(pdf_path, client, MD)

    assert sum(client.image_counts_seen) == 3  # one capped batch, not 5 full ones
    assert len(client.image_counts_seen) == 1  # exactly one classification call, not five


def test_short_document_sends_all_its_pages_in_one_call(tmp_path):
    pdf_path = tmp_path / "short_invoice.pdf"
    _make_pdf(pdf_path, n_pages=2)

    client = CountingVisionClient()
    process_document(pdf_path, client, MD)

    assert client.image_counts_seen == [2]  # under the cap, one batch, nothing trimmed