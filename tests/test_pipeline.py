import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookable_payable.llm.base import VisionClient
from bookable_payable.masterdata import MasterData
from bookable_payable.pipeline import process_payable_candidate

MD = MasterData.load(Path(__file__).resolve().parents[1] / "master_data")


class ScriptedVisionClient(VisionClient):
    """Returns a queue of canned responses in order — one per call. Used here to
    simulate: attempt 1 gets a header wrong, attempt 2 (after our feedback) gets
    it right. Each "extraction round" is 2 calls (header, then line items)."""

    def __init__(self, canned_responses: list[dict]):
        self.canned_responses = list(canned_responses)
        self.calls: list[dict] = []

    def extract_json(self, *, system_prompt, user_prompt, images_b64_png, max_tokens=900):
        self.calls.append({"user_prompt": user_prompt})
        return self.canned_responses.pop(0)


WRONG_HEADER = {  # model saw the real total but missed the management fee + taxes entirely
    "invoice_number": "SI-TEST", "currency": "THB", "declared_grand_total": "8161.92",
    "header_charges": [], "header_taxes": [],
}
LINE_ITEMS = {"line_items": [{"description": "Staff", "quantity": "12", "unit_price": "600.00", "line_total": "7200.00"}]}

CORRECT_HEADER = {  # the real HLD-01 answer, from our earlier hand-verification
    "invoice_number": "SI-TEST", "currency": "THB", "declared_grand_total": "8161.92",
    "header_charges": [{"label": "Management Fee", "amount": "648.00"}],
    "header_taxes": [
        {"label": "VAT", "rate_percent": "7", "amount": "549.36"},
        {"label": "Withholding Tax", "rate_percent": "3", "amount": "-235.44"},
    ],
}


def test_retry_succeeds_on_second_attempt_with_feedback():
    client = ScriptedVisionClient([
        WRONG_HEADER, LINE_ITEMS,      # attempt 1: header + line items, wrong
        CORRECT_HEADER, LINE_ITEMS,    # attempt 2 (after feedback): correct
    ])
    outcome = process_payable_candidate(["page1"], client, MD)
    assert outcome["diagnostics"]["resolved"] is True
    assert len(outcome["diagnostics"]["attempts"]) == 2
    assert outcome["payable"]["gross_total"] == "8161.92"


def test_feedback_contains_discrepancy_size_not_a_suggested_fix():
    client = ScriptedVisionClient([WRONG_HEADER, LINE_ITEMS, CORRECT_HEADER, LINE_ITEMS])
    process_payable_candidate(["page1"], client, MD)
    # the SECOND header call (index 2) should carry feedback mentioning the gap
    second_header_call_prompt = client.calls[2]["user_prompt"]
    assert "discrepancy" in second_header_call_prompt.lower()
    assert "961.92" in second_header_call_prompt or "-961.92" in second_header_call_prompt  # 8161.92 - 7200.00


def test_exhausted_retries_still_returns_a_grounded_payable_not_none():
    # every attempt returns the same wrong (incomplete) answer — retries never converge
    client = ScriptedVisionClient([WRONG_HEADER, LINE_ITEMS] * 10)  # plenty for max retries + 1
    outcome = process_payable_candidate(["page1"], client, MD)
    assert outcome["diagnostics"]["resolved"] is False
    # still a real, usable payable — not dropped, not None
    assert outcome["payable"]["invoice_number"] == "SI-TEST"
    assert outcome["payable"]["gross_total"] == "8161.92"  # the document's own stated figure, kept as-is
    # crucially: no charge/tax was invented just to make the numbers foot —
    # the payable honestly reflects that nothing was found, not a fabricated fix
    assert outcome["payable"]["extra_charges"] == ""
    assert outcome["payable"]["taxes"] == []
    last_attempt = outcome["diagnostics"]["attempts"][-1]
    assert last_attempt["verify_result"]["matches"] is False


def test_first_attempt_success_never_calls_extraction_twice():
    client = ScriptedVisionClient([CORRECT_HEADER, LINE_ITEMS])  # only enough for ONE attempt
    outcome = process_payable_candidate(["page1"], client, MD)
    assert outcome["diagnostics"]["resolved"] is True
    assert len(outcome["diagnostics"]["attempts"]) == 1
    assert len(client.calls) == 2  # header + line items, no retry calls made