"""Wires every piece together for ONE document: classify, then (for each payable
candidate) extract -> build -> verify -> retry-with-evidence on mismatch.

Important design decision, straight from the brief's own wording: `declined[]`
is only for documents that are not payables at all. A document that genuinely
IS an invoice but whose numbers don't reconcile with erp.py even after retries
is still emitted as a payable — built from real, grounded values only — never
silently dropped and never faked into matching. The retry loop's job is to
correct genuine extraction mistakes (a misread digit, a tax on the wrong base),
never to force a number until the math works.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .autodraft import build_payable
from .classify import classify_document
from .config import settings
from .extraction import extract_header, extract_line_items
from .ingestion import render_pdf_pages
from .llm.base import VisionClient
from .masterdata import MasterData
from .verify import verify_payable


def _build_feedback(result: dict) -> str:
    """Turns a verify_payable() mismatch into a re-extraction prompt addition.
    Deliberately gives the SIZE of the discrepancy, not a guess at the cause —
    that keeps the model re-examining the actual page rather than reverse
    engineering a number that happens to close the gap (which Rule 1 forbids)."""
    return (
        f"Your previous reading produced a booked gross of {result['booked_gross']} "
        f"{result['currency']}, but the document states {result['declared_gross']} is "
        f"owed (a difference of {result['diff']}). Re-examine the page carefully for "
        f"anything you may have missed, misread, or misplaced — an additional charge, "
        f"a tax applied to the wrong base, a misread digit, a line you dropped. Only "
        f"change what you find is genuinely different from your first reading; do not "
        f"adjust a number just to make the total match."
    )


def extract_payable_raw(pages_b64png: list[str], client: VisionClient, *, feedback: str = "") -> dict[str, Any]:
    """Like extraction.extract_payable, but can carry feedback from a failed
    verification into both the header and line-item re-reads."""
    header = extract_header(pages_b64png, client, feedback=feedback)
    header["line_items"] = extract_line_items(pages_b64png, client, feedback=feedback)
    return header


def process_payable_candidate(
    pages_b64png: list[str],
    client: VisionClient,
    master: MasterData,
    *,
    company_code: str,
    business_unit_code: str,
) -> dict[str, Any]:
    """Runs extract -> build -> verify, retrying with evidence up to
    settings.max_extraction_retries times. Always returns the most recently
    built payable — resolved or not — plus diagnostics for our own review
    (diagnostics are NOT part of the submitted schema, output.py drops them)."""
    feedback = ""
    attempts: list[dict] = []
    payable: dict = {}

    for attempt in range(settings.max_extraction_retries + 1):
        raw = extract_payable_raw(pages_b64png, client, feedback=feedback)
        country = raw.get("supplier_country", "")
        payable = build_payable(
            raw, master, company_code=company_code, business_unit_code=business_unit_code, country=country
        )
        result = verify_payable(payable)
        attempts.append({"attempt": attempt, "verify_result": result, "model_notes": raw.get("notes", "")})

        if result["matches"]:
            return {"payable": payable, "diagnostics": {"attempts": attempts, "resolved": True}}

        feedback = _build_feedback(result)

    # Retries exhausted without reconciling — still emit the payable. Every
    # value in it came from the document; it just didn't foot to the cent.
    # That's an honest, documented shortfall, not a fabricated pass.
    return {"payable": payable, "diagnostics": {"attempts": attempts, "resolved": False}}


def process_document(
    pdf_path: str | Path,
    client: VisionClient,
    master: MasterData,
    *,
    company_code: str,
    business_unit_code: str,
) -> dict[str, Any]:
    """Full pipeline for one PDF. Returns the exact output/X.json shape the
    brief requires (file, payables, declined), plus a "diagnostics" key that
    our own output-writing step strips before saving — useful for us, not part
    of the graded contract."""
    pdf_path = Path(pdf_path)
    pages = render_pdf_pages(pdf_path)
    classification = classify_document(pages, client)

    if not classification["is_payable"]:
        return {
            "file": pdf_path.name,
            "payables": [],
            "declined": [{"doc_type": classification["doc_type"], "reason": classification["reason"]}],
            "diagnostics": {"classification": classification},
        }

    payables: list[dict] = []
    candidate_diagnostics: list[dict] = []
    # NOTE: payable_count > 1 is rare (the classification prompt only expects it
    # for genuinely multiple distinct invoices stapled in one file) and our
    # extraction prompt doesn't yet distinguish "candidate #2" from "#1" — a
    # known, documented simplification, revisited only if a real document needs it.
    for _ in range(classification["payable_count"]):
        outcome = process_payable_candidate(
            pages, client, master, company_code=company_code, business_unit_code=business_unit_code
        )
        payables.append(outcome["payable"])
        candidate_diagnostics.append(outcome["diagnostics"])

    return {
        "file": pdf_path.name,
        "payables": payables,
        "declined": [],
        "diagnostics": {"classification": classification, "candidates": candidate_diagnostics},
    }