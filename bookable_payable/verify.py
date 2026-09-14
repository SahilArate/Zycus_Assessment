"""Calls erp.py — the deterministic ERP validator — and reports whether our
payable reproduces the document's own stated total, to the cent. This module
never modifies a payable to force a match — that decision belongs one layer
up, where we still have the document image to check evidence against, not
just numbers.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from erp import erp_book  # the deterministic ERP validator we must never re-implement or modify

_TOLERANCE = 0.005  # half a cent, to absorb float/str round-tripping only


def _to_float(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def verify_payable(payable: dict) -> dict:
    """Returns {"booked_gross", "declared_gross", "diff", "matches", "currency"}.
    declared_gross/diff/matches are None when the payable has no declared gross_total
    to compare against (shouldn't happen for a real payable, but never crash on it)."""
    result = erp_book(payable)
    booked = result["will_book_gross"]
    declared = _to_float(payable.get("gross_total"))
    if declared is None:
        return {
            "booked_gross": booked,
            "declared_gross": None,
            "diff": None,
            "matches": False,
            "currency": result["currency"],
        }
    diff = round(booked - declared, 2)
    return {
        "booked_gross": booked,
        "declared_gross": declared,
        "diff": diff,
        "matches": abs(diff) <= _TOLERANCE,
        "currency": result["currency"],
    }