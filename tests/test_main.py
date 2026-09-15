import json
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as main_module
from bookable_payable.llm.base import VisionClient


class ScriptedClient(VisionClient):
    """Returns a fixed classification for every document, regardless of content
    — enough to prove main.py's orchestration, file-writing, and error handling
    without needing a real API key or real documents."""

    def __init__(self, responses_by_call_index):
        self.responses = responses_by_call_index
        self.call_index = 0

    def extract_json(self, *, system_prompt, user_prompt, images_b64_png, max_tokens=900):
        r = self.responses[min(self.call_index, len(self.responses) - 1)]
        self.call_index += 1
        return r


def _make_pdf(path: Path):
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()


def test_main_writes_one_output_file_per_input_pdf(tmp_path, monkeypatch, capsys):
    docs_dir = tmp_path / "documents"
    out_dir = tmp_path / "output"
    docs_dir.mkdir()
    _make_pdf(docs_dir / "not_a_payable.pdf")
    _make_pdf(docs_dir / "also_not_a_payable.pdf")

    fake_client = ScriptedClient([
        {"segments": [{"pages": [1], "is_payable": False, "new_payable": False,
                       "invoice_number": "", "doc_type": "delivery_note", "reason": "just a delivery note"}]},
    ])
    monkeypatch.setattr(main_module, "get_vision_client", lambda: fake_client)
    monkeypatch.setattr(sys, "argv", ["main.py", "--documents-dir", str(docs_dir), "--output-dir", str(out_dir)])

    main_module.main()

    assert (out_dir / "not_a_payable.json").exists()
    assert (out_dir / "also_not_a_payable.json").exists()
    data = json.loads((out_dir / "not_a_payable.json").read_text())
    assert set(data.keys()) == {"file", "payables", "declined"}  # diagnostics stripped, exact submission shape
    assert data["declined"][0]["doc_type"] == "delivery_note"

    printed = capsys.readouterr().out
    assert "Processed 2 documents" in printed


def test_main_one_bad_document_does_not_stop_the_run(tmp_path, monkeypatch, capsys):
    docs_dir = tmp_path / "documents"
    out_dir = tmp_path / "output"
    docs_dir.mkdir()
    (docs_dir / "corrupt.pdf").write_bytes(b"not a real pdf at all")  # will fail to open
    _make_pdf(docs_dir / "fine.pdf")

    fake_client = ScriptedClient([
        {"segments": [{"pages": [1], "is_payable": False, "new_payable": False,
                       "invoice_number": "", "doc_type": "delivery_note", "reason": "fine"}]},
    ])
    monkeypatch.setattr(main_module, "get_vision_client", lambda: fake_client)
    monkeypatch.setattr(sys, "argv", ["main.py", "--documents-dir", str(docs_dir), "--output-dir", str(out_dir)])

    main_module.main()

    # BOTH files got an output — the corrupt one didn't crash the run or vanish silently
    assert (out_dir / "corrupt.json").exists()
    assert (out_dir / "fine.json").exists()
    corrupt_result = json.loads((out_dir / "corrupt.json").read_text())
    assert corrupt_result["payables"] == []
    assert "error" in corrupt_result["declined"][0]["reason"].lower()

    printed = capsys.readouterr().out
    assert "Hard processing errors: 1" in printed