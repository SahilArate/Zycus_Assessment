"""Wires every piece together for ONE document: classify, then (for each payable
candidate) extract -> build -> verify -> retry-with-evidence on mismatch.

Two important design decisions, both revised after review:

1. `declined[]` covers two cases: a document that isn't a payable at all, AND a
   document that IS one but could not be reconciled with the ERP validator
   after retries. We do not submit a figure we know doesn't check out — per
   the brief's own framing, recognizing a document can't be solved and saying
   so honestly is worth more than code that pretends otherwise.

2. The retry loop's job is to correct genuine extraction mistakes (a misread
   digit, a tax on the wrong base), never to force a number until the math
   works — it's told the SIZE of a discrepancy, never a suggested fix.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .autodraft import build_payable
from .classify import classify_document
from .config import settings
from .extraction import extract_header, extract_line_items
from .ingestion import render_pdf_pages, select_pages_for_model
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


def _build_candidate_hint(candidate_index: int, candidate_count: int) -> str:
    """Only non-empty when classification found MORE THAN ONE payable in a
    single file (rare — genuinely separate invoices stapled together). Without
    this, extracting "candidate 2 of 2" with no distinguishing instruction
    would just re-read the same invoice as candidate 1 — a real bug, not a
    hypothetical one."""
    if candidate_count <= 1:
        return ""
    return (
        f"IMPORTANT: this document contains {candidate_count} separate, distinct "
        f"payables (e.g. multiple invoices stapled together), not one. You are "
        f"extracting candidate {candidate_index + 1} of {candidate_count}. Identify "
        f"the distinct invoice numbers/totals present and extract ONLY the "
        f"{'first' if candidate_index == 0 else f'{candidate_index + 1}(th)'} distinct "
        f"one you find, by position — do not repeat a payable you'd already extract "
        f"for a different candidate index."
    )


def extract_payable_raw(
    pages_b64png: list[str], client: VisionClient, *, feedback: str = "", candidate_hint: str = ""
) -> dict[str, Any]:
    """Like extraction.extract_payable, but can carry feedback from a failed
    verification, and/or a candidate_hint when a file holds more than one
    payable, into both the header and line-item re-reads."""
    combined = " ".join(x for x in (candidate_hint, feedback) if x)
    header = extract_header(pages_b64png, client, feedback=combined)
    header["line_items"] = extract_line_items(pages_b64png, client, feedback=combined)
    return header


def process_payable_candidate(
    pages_b64png: list[str],
    client: VisionClient,
    master: MasterData,
    *,
    candidate_index: int = 0,
    candidate_count: int = 1,
) -> dict[str, Any]:
    """Runs extract -> build -> verify, retrying with evidence up to
    settings.max_extraction_retries times. Returns the most recently built
    payable plus diagnostics["resolved"] — the caller (process_document)
    decides whether an unresolved result becomes a declined entry.
    diagnostics are NOT part of the submitted schema; main.py strips them."""
    candidate_hint = _build_candidate_hint(candidate_index, candidate_count)
    feedback = ""
    attempts: list[dict] = []
    payable: dict = {}

    for attempt in range(settings.max_extraction_retries + 1):
        print(f"    extraction attempt {attempt + 1}/{settings.max_extraction_retries + 1} (may pause ~20s if rate limited)...", flush=True)
        raw = extract_payable_raw(pages_b64png, client, feedback=feedback, candidate_hint=candidate_hint)
        country = raw.get("supplier_country", "")
        payable = build_payable(raw, master, country=country)
        result = verify_payable(payable)
        attempts.append({"attempt": attempt, "verify_result": result, "model_notes": raw.get("notes", "")})
        print(f"    -> booked {result['booked_gross']} vs declared {result['declared_gross']} ({'MATCH' if result['matches'] else 'mismatch'})")

        if result["matches"]:
            return {"payable": payable, "diagnostics": {"attempts": attempts, "resolved": True}}

        feedback = _build_feedback(result)

    # Retries exhausted without reconciling. The caller declines this
    # candidate rather than submitting it — see module docstring.
    return {"payable": payable, "diagnostics": {"attempts": attempts, "resolved": False}}


def process_document(
    pdf_path: str | Path,
    client: VisionClient,
    master: MasterData,
) -> dict[str, Any]:
    """Full pipeline for one PDF. Returns the exact output/X.json shape the
    brief requires (file, payables, declined), plus a "diagnostics" key that
    main.py strips before saving — useful for us, not part of the graded
    contract."""
    pdf_path = Path(pdf_path)
    pages = render_pdf_pages(pdf_path)
    pages = select_pages_for_model(pages)  # cap once, before ANY model call sees them
    print("  classifying...", end=" ", flush=True)
    classification = classify_document(pages, client)
    print(f"-> {classification['doc_type']}, payable={classification['is_payable']}")

    if not classification["is_payable"]:
        return {
            "file": pdf_path.name,
            "payables": [],
            "declined": [{"doc_type": classification["doc_type"], "reason": classification["reason"]}],
            "diagnostics": {"classification": classification},
        }

    payables: list[dict] = []
    declined: list[dict] = []
    candidate_diagnostics: list[dict] = []
    for candidate_index in range(classification["payable_count"]):
        outcome = process_payable_candidate(
            pages, client, master, candidate_index=candidate_index, candidate_count=classification["payable_count"]
        )
        candidate_diagnostics.append(outcome["diagnostics"])
        if outcome["diagnostics"]["resolved"]:
            payables.append(outcome["payable"])
        else:
            last_result = outcome["diagnostics"]["attempts"][-1]["verify_result"]
            declined.append({
                "doc_type": classification["doc_type"],
                "reason": (
                    f"Extracted as a likely {classification['doc_type']} but could not reconcile with the "
                    f"ERP validator after {len(outcome['diagnostics']['attempts'])} attempt(s) "
                    f"(booked {last_result['booked_gross']} vs declared {last_result['declared_gross']}, "
                    f"diff {last_result['diff']}). Declining rather than submitting an unverified figure."
                ),
            })

    return {
        "file": pdf_path.name,
        "payables": payables,
        "declined": declined,
        "diagnostics": {"classification": classification, "candidates": candidate_diagnostics},
    }