"""The one command the brief asks for: run the whole pipeline over documents/
and write output/X.json for every document, in every case — including when
something goes wrong. One bad PDF must never stop the other 41 from processing.

Usage:
    python main.py                          # processes documents/, writes output/
    python main.py --documents-dir path/to  # override input folder
    python main.py --output-dir path/to     # override output folder
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from bookable_payable.llm import get_vision_client
from bookable_payable.masterdata import MasterData
from bookable_payable.pipeline import process_document

SUBMITTED_KEYS = ("file", "payables", "declined")  # everything else (diagnostics) is ours only


def _strip_to_submission_shape(result: dict) -> dict:
    return {k: result[k] for k in SUBMITTED_KEYS}


def _fallback_output(pdf_path: Path, error: Exception) -> dict:
    """What we write when a document genuinely can't be processed (corrupt PDF,
    unexpected crash) — still one output file per input, as required, with an
    honest reason rather than silently missing from the results."""
    return {
        "file": pdf_path.name,
        "payables": [],
        "declined": [{"doc_type": "unknown", "reason": f"processing error, not a determination this isn't a payable: {error}"}],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents-dir", default="documents")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    documents_dir = Path(args.documents_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_paths = sorted(documents_dir.glob("*.pdf"))
    if not pdf_paths:
        print(f"No PDFs found in {documents_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading master data...")
    master = MasterData.load("master_data")
    print(f"Setting up vision client...")
    client = get_vision_client()

    total_payables = 0
    total_declined = 0
    total_errors = 0
    total_unresolved = 0  # payables emitted despite not reconciling with erp.py
    started_at = time.time()

    for i, pdf_path in enumerate(pdf_paths, start=1):
        print(f"[{i}/{len(pdf_paths)}] {pdf_path.name} ...", end=" ", flush=True)
        try:
            result = process_document(pdf_path, client, master)
            unresolved_here = sum(
                1 for c in result["diagnostics"].get("candidates", []) if not c.get("resolved", True)
            )
            total_unresolved += unresolved_here
            output = _strip_to_submission_shape(result)
            status = f"{len(output['payables'])} payable(s), {len(output['declined'])} declined"
            if unresolved_here:
                status += f" (⚠ {unresolved_here} did not reconcile with erp.py)"
            print(status)
        except Exception as e:  # noqa: BLE001 — deliberately broad: one bad file must not kill the run
            print(f"ERROR: {e}")
            traceback.print_exc(file=sys.stderr)
            output = _fallback_output(pdf_path, e)
            total_errors += 1

        total_payables += len(output["payables"])
        total_declined += len(output["declined"])

        out_path = output_dir / f"{pdf_path.stem}.json"
        out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))

    elapsed = time.time() - started_at
    print()
    print("=" * 60)
    print(f"Processed {len(pdf_paths)} documents in {elapsed:.0f}s")
    print(f"  Payables emitted:  {total_payables}")
    print(f"  Declined:          {total_declined}")
    print(f"  Did not reconcile with erp.py (still emitted honestly): {total_unresolved}")
    print(f"  Hard processing errors: {total_errors}")
    print(f"Output written to: {output_dir}/")


if __name__ == "__main__":
    main()