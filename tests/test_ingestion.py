import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookable_payable.ingestion import batch_pages, select_pages_for_model


def test_short_document_unchanged():
    pages = ["p1", "p2", "p3"]
    assert select_pages_for_model(pages, max_pages=5) == pages


def test_exactly_at_limit_unchanged():
    pages = ["p1", "p2", "p3", "p4", "p5"]
    assert select_pages_for_model(pages, max_pages=5) == pages


def test_long_document_takes_first_and_last():
    pages = [f"p{i}" for i in range(1, 21)]  # 20 pages, like DU-02/DU-05s
    result = select_pages_for_model(pages, max_pages=5)
    assert len(result) == 5
    assert result[0] == "p1"  # header info near the start
    assert result[-1] == "p20"  # totals often on the final page


def test_single_page_document():
    assert select_pages_for_model(["p1"], max_pages=5) == ["p1"]

def test_batch_pages_covers_every_page_exactly_once():
    pages = [f"p{i}" for i in range(1, 21)]  # 20 pages, like DU-02
    batches = batch_pages(pages, batch_size=3)
    all_pages = [p for batch in batches for p in batch]
    assert all_pages == pages  # nothing dropped, nothing reordered, nothing duplicated


def test_batch_pages_batch_sizes():
    pages = [f"p{i}" for i in range(1, 21)]
    batches = batch_pages(pages, batch_size=3)
    assert len(batches) == 7  # 6 full batches of 3 + one of 2
    assert all(len(b) <= 3 for b in batches)
    assert len(batches[-1]) == 2


def test_batch_pages_short_document_is_one_batch():
    pages = ["p1", "p2"]
    batches = batch_pages(pages, batch_size=3)
    assert batches == [["p1", "p2"]]


def test_batch_pages_empty_document():
    assert batch_pages([], batch_size=3) == []