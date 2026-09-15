"""Wires everything together for ONE document: render every page, batch them,
classify every batch, merge the classification evidence into payable page-
groups (see _merge_segments_into_payables), then for each group: extract ->
build -> verify -> retry-with-evidence on mismatch.

This replaced an earlier version that capped a document to 3 pages BEFORE
classification ever ran — meaning a 20-page document only ever had 3 pages
seen at all, and a payable starting mid-document could be missed entirely.
Now every page reaches classification; only extraction (which has a real,
external 3-images-per-request hard cap) ever narrows down to a subset, and it
narrows to a specific payable's OWN real pages, not a blind document-wide guess.

Three important design decisions:

1. `declined[]` covers two cases: a document (or part of one) that isn't a
   payable at all, AND a payable that could not be reconciled with the ERP
   validator after retries. We do not submit a figure we know doesn't check
   out — per the brief's own framing, recognizing a document can't be solved
   and saying so honestly is worth more than code that pretends otherwise.

2. The retry loop's job is to correct genuine extraction mistakes (a misread
   digit, a tax on the wrong base), never to force a number until the math
   works — it's told the SIZE of a discrepancy, never a suggested fix.

3. The batch-merge step never just sums payable counts across batches. A
   payable spanning several batches must become ONE group, not one per batch
   it happened to touch — see _merge_segments_into_payables.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .autodraft import build_payable
from .classify import classify_batch
from .config import settings
from .extraction import extract_header, extract_line_items
from .ingestion import batch_pages, render_pdf_pages, select_pages_for_model
from .llm.base import VisionClient
from .masterdata import MasterData
from .verify import verify_payable


def _merge_segments_into_payables(all_segments: list[dict]) -> tuple[list[dict], list[dict]]:
    """Groups classification segments (flattened across all batches, in
    original page order) into payable page-groups. Deliberately does NOT just
    sum payable_count across batches — a payable spanning multiple batches
    becomes ONE group, not one per batch it happened to touch.

    Grouping key: invoice_number when visible — the most reliable way to link
    a continuation back to its start even many batches later. When a
    continuation segment (new_payable=false) has no invoice_number, it's
    attached to whichever payable is currently "open" (the most recently
    started/continued one) rather than inventing a new payable from evidence
    that can't be identified — the same conservative default the brief asks
    for elsewhere (an honest blank beats a guess).

    Returns (payable_groups, declined_segments) — declined_segments are the
    non-payable pieces of evidence (delivery notes, statements, etc.), kept so
    the caller can build an honest declined[] reason when there's no payable
    at all."""
    payables: list[dict] = []
    declined_segments: list[dict] = []
    open_payable: dict | None = None

    def _find_by_invoice_number(num: str) -> dict | None:
        if not num:
            return None
        for p in payables:
            if p["invoice_number"] and p["invoice_number"] == num:
                return p
        return None

    for seg in all_segments:
        if not seg["is_payable"]:
            declined_segments.append(seg)
            continue

        existing = _find_by_invoice_number(seg["invoice_number"])
        if seg["new_payable"] and not existing:
            group = {"invoice_number": seg["invoice_number"], "doc_type": seg["doc_type"], "pages": list(seg["pages"])}
            payables.append(group)
            open_payable = group
            continue

        target = existing or open_payable
        if target is None:
            # a continuation with nothing open to attach to (e.g. the very first
            # segment in the document already claims to be one) — treat it
            # defensively as a new payable rather than silently dropping pages
            group = {"invoice_number": seg["invoice_number"], "doc_type": seg["doc_type"], "pages": list(seg["pages"])}
            payables.append(group)
            open_payable = group
            continue

        target["pages"].extend(seg["pages"])
        if not target["invoice_number"] and seg["invoice_number"]:
            target["invoice_number"] = seg["invoice_number"]
        open_payable = target

    for p in payables:
        p["pages"] = sorted(set(p["pages"]))
    return payables, declined_segments


def _combine_declined_reasons(declined_segments: list[dict], total_pages: int) -> str:
    if not declined_segments:
        return f"No payable evidence found across {total_pages} page(s)."
    reasons: list[str] = []
    for seg in declined_segments:
        r = seg.get("reason") or ""
        if r and r not in reasons:
            reasons.append(r)
    return " ".join(reasons) if reasons else f"No payable evidence found across {total_pages} page(s)."


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


def process_payable_candidate(pages_b64png: list[str], client: VisionClient, master: MasterData) -> dict[str, Any]:
    """Runs extract -> build -> verify, retrying with evidence up to
    settings.max_extraction_retries times, on ONE payable's own already-
    selected pages. Returns the most recently built payable plus
    diagnostics["resolved"] — the caller (process_document) decides whether an
    unresolved result becomes a declined entry. diagnostics are NOT part of
    the submitted schema; main.py strips them."""
    feedback = ""
    attempts: list[dict] = []
    payable: dict = {}

    for attempt in range(settings.max_extraction_retries + 1):
        print(f"    extraction attempt {attempt + 1}/{settings.max_extraction_retries + 1} (may pause ~20s if rate limited)...", flush=True)
        raw = extract_payable_raw(pages_b64png, client, feedback=feedback)
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


def process_document(pdf_path: str | Path, client: VisionClient, master: MasterData) -> dict[str, Any]:
    """Full pipeline for one PDF. Returns the exact output/X.json shape the
    brief requires (file, payables, declined), plus a "diagnostics" key that
    main.py strips before saving — useful for us, not part of the graded
    contract."""
    pdf_path = Path(pdf_path)
    all_pages = render_pdf_pages(pdf_path)
    batches = batch_pages(all_pages)

    all_segments: list[dict] = []
    batch_diagnostics: list[dict] = []
    start = 0
    for batch_images in batches:
        page_numbers = list(range(start + 1, start + 1 + len(batch_images)))
        print(f"  classifying pages {page_numbers}...", end=" ", flush=True)
        segments = classify_batch(batch_images, page_numbers, client)
        print(f"-> {len(segments)} segment(s)")
        all_segments.extend(segments)
        batch_diagnostics.append({"page_numbers": page_numbers, "segments": segments})
        start += len(batch_images)

    payable_groups, declined_segments = _merge_segments_into_payables(all_segments)

    if not payable_groups:
        doc_type = declined_segments[0]["doc_type"] if declined_segments else "other"
        reason = _combine_declined_reasons(declined_segments, total_pages=len(all_pages))
        return {
            "file": pdf_path.name,
            "payables": [],
            "declined": [{"doc_type": doc_type, "reason": reason}],
            "diagnostics": {"batches": batch_diagnostics},
        }

    payables: list[dict] = []
    declined: list[dict] = []
    candidate_diagnostics: list[dict] = []
    for group in payable_groups:
        group_page_images = [all_pages[p - 1] for p in group["pages"]]
        pages_for_extraction = select_pages_for_model(group_page_images)
        outcome = process_payable_candidate(pages_for_extraction, client, master)
        candidate_diagnostics.append({"pages": group["pages"], **outcome["diagnostics"]})
        if outcome["diagnostics"]["resolved"]:
            payables.append(outcome["payable"])
        else:
            last_result = outcome["diagnostics"]["attempts"][-1]["verify_result"]
            doc_type = group["doc_type"] or "invoice"
            declined.append({
                "doc_type": doc_type,
                "reason": (
                    f"Extracted as a likely {doc_type} (pages {group['pages']}) but could not reconcile "
                    f"with the ERP validator after {len(outcome['diagnostics']['attempts'])} attempt(s) "
                    f"(booked {last_result['booked_gross']} vs declared {last_result['declared_gross']}, "
                    f"diff {last_result['diff']}). Declining rather than submitting an unverified figure."
                ),
            })

    return {
        "file": pdf_path.name,
        "payables": payables,
        "declined": declined,
        "diagnostics": {"batches": batch_diagnostics, "candidates": candidate_diagnostics},
    }