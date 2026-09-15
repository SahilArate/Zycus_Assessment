# Zycus AI/ML Assessment — Autodraft Pipeline

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Tests](https://img.shields.io/badge/tests-82%20passing-brightgreen)
![Docker](https://img.shields.io/badge/docker-ready-2496ED)
![erp.py](https://img.shields.io/badge/erp.py-unmodified-lightgrey)

A vision-grounded document intelligence pipeline that turns a folder of
**unlabeled supplier documents** — invoices, credit memos, delivery notes,
purchase orders, in mixed languages, currencies, and layouts — into
structured, **ERP-verified** payables, following the contract in
[`AUTODRAFT_SCHEMA.md`](./AUTODRAFT_SCHEMA.md).

> **The core idea in one sentence:** a vision-language model *reads* every
> page; it never *decides* whether what it read is correct. That job belongs
> to `erp.py` — the one component this system is not allowed to touch.

---

## Table of contents

- [Zycus AI/ML Assessment — Autodraft Pipeline](#zycus-aiml-assessment--autodraft-pipeline)
  - [Table of contents](#table-of-contents)
  - [How it works](#how-it-works)
  - [Walking one real document through the pipeline](#walking-one-real-document-through-the-pipeline)
  - [What we used, and why](#what-we-used-and-why)
  - [Why this architecture beats "PDF → LLM → JSON"](#why-this-architecture-beats-pdf--llm--json)
  - [Setup](#setup)
    - [Prerequisites](#prerequisites)
    - [1. Clone and create a virtual environment](#1-clone-and-create-a-virtual-environment)
    - [2. Install dependencies](#2-install-dependencies)
    - [3. Configure credentials](#3-configure-credentials)
    - [4. Verify the setup (no API key required for this step)](#4-verify-the-setup-no-api-key-required-for-this-step)
    - [5. Add input documents and run](#5-add-input-documents-and-run)
  - [Running it](#running-it)
    - [With Docker](#with-docker)
  - [Testing](#testing)
  - [Project structure](#project-structure)
  - [Known, honestly-documented limitations](#known-honestly-documented-limitations)

---

## How it works

```
                              ┌────────────────────────┐
                              │   documents/*.pdf       │
                              └───────────┬─────────────┘
                                          │
                     render EVERY page (PyMuPDF, no pre-cropping)
                                          │
                                          ▼
                     batch pages, ≤3 per call (the vision model's
                     hard per-request image limit)
                                          │
                                          ▼
              ┌───────────────────────────────────────────────┐
              │  CLASSIFY each batch  (multimodal VLM call)    │
              │  → payable evidence? which doc type?           │
              │  → NEW payable, or continuation of one         │
              │    already seen earlier in the document?       │
              └───────────────────────┬───────────────────────┘
                                          │
                     MERGE segments into payable groups
                     (pure Python — pages belonging to the
                     same payable become ONE group, no matter
                     which batch they landed in)
                                          │
                    ┌─────────────────────┴─────────────────────┐
                    │        for EACH payable group:             │
                    │                                             │
                    │   EXTRACT header  ──►  EXTRACT line items   │
                    │        (VLM)                (VLM)           │
                    └─────────────────────┬─────────────────────┘
                                          │
              ┌───────────────────────────────────────────────┐
              │  DETERMINISTIC PYTHON NORMALIZATION            │
              │  locale-safe numbers · tax-inclusive → net     │
              │  price · "per-100-units" → per-unit price      │
              │  — arithmetic on printed facts, never guessed  │
              └───────────────────────┬───────────────────────┘
                                          │
              ┌───────────────────────────────────────────────┐
              │  MASTER-DATA RESOLUTION                        │
              │  indexed exact match → confidence-margin       │
              │  fuzzy match → honest blank if ambiguous       │
              │  (supplier · tax · payment term · PO · buyer)  │
              └───────────────────────┬───────────────────────┘
                                          │
              ┌───────────────────────────────────────────────┐
              │  REPRESENTABILITY GATE                         │
              │  can this be expressed WITHOUT misstating      │
              │  what the document says?                       │
              └──────────┬────────────────────────┬───────────┘
                       NO │                        │ YES
                          ▼                        ▼
                    ┌──────────┐      ┌─────────────────────────────┐
                    │ DECLINE  │      │  ERP VALIDATION (erp.py)      │
                    └──────────┘      │  the one component we never   │
                                       │  modify, wrote, or influence  │
                                       └──────────┬─────────┬─────────┘
                                              MATCH│         │MISMATCH
                                                   ▼         ▼
                                            ┌──────────┐  retry with the
                                            │ SUBMIT   │  SIZE of the gap
                                            └──────────┘  as evidence
                                                              │
                                                   still MISMATCH after
                                                   capped retries
                                                              │
                                                              ▼
                                                        ┌──────────┐
                                                        │ DECLINE  │
                                                        └──────────┘
```

**One JSON file per input PDF, always** — `{"file", "payables", "declined"}`
— written via idempotent, isolated per-document processing: a hard failure
on one file is caught, logged honestly, and never allowed to abort the run.

---

## Walking one real document through the pipeline

Abstract diagrams are easy to nod along to and hard to trust. Here's what
actually happened on a real document from this kit, `HLD-01.pdf` — a Thai
invoice with a 9% "management fee," used because it's the clearest example
of every stage doing real work, not just passing data through:

| Stage | What happened |
|---|---|
| **Classify** | VLM reads the page images → `is_payable: true`, `doc_type: invoice`, `new_payable: true` |
| **Extract** | VLM reports the printed fields *as printed*: a staff-hire line, a 9% management fee, 7% VAT, a withholding-tax line — **no arithmetic performed by the model** |
| **Normalize (Python)** | Deterministic code works out that VAT applies to the line total **plus** the management fee (not the line alone) — the "obvious" naive calculation would have been off by a real, non-trivial amount |
| **Master-data resolution** | Supplier and tax codes resolved by indexed exact-match; no ambiguity in this document |
| **Representability gate** | Passes — every printed value has a home in the schema |
| **ERP validation** | `erp.py` recomputes the booked gross and compares it to the document's own stated total: **8,161.92 THB, to the cent** — an exact match |
| **Result** | `payable["extra_charges"]` and the negative withholding-tax line both come through correctly; the whole record reconciles — proven by `test_hld01_builds_and_matches_oracle`, built from this real, hand-verified document, not a synthetic fixture |

The lesson this document taught the system (and why the normalization layer
exists at all): **matching the total isn't the same as being right.** Two
different structures can add up to the same number — the grader checks the
shape, not just the sum. Full write-up in [`DESIGN.md`](./DESIGN.md).

---

## What we used, and why

| Choice | Why this, not the obvious alternative |
|---|---|
| **Vision-language model (Groq / Anthropic), not OCR + text-LLM** | A two-stage OCR → text pipeline throws away layout: which number is on which line, which tax sits under which charge. A VLM reads the page as a human would — spatially — which matters enormously on documents where the tax base depends on which charges sit where on the page. |
| **Provider-agnostic client interface** (`llm/base.py`) | External vision-model lineups change (this happened twice mid-development). The pipeline code imports one abstract method, never a specific SDK — swapping Groq for Anthropic is a one-line config change, not a rewrite. |
| **PyMuPDF for rendering, not a PDF-text-extraction library** | Most of this kit's documents are scanned images with no embedded text layer at all (confirmed directly — `pdfplumber.extract_text()` returned empty on the long documents). Rendering to page images is the only approach that works across the whole kit, not just the minority with real text layers. |
| **RapidFuzz with a confidence *margin*, not a bare threshold** | A threshold alone (`best_score >= 87`) still lets a 91-vs-90 near-tie through confidently. Master-data resolution compares the top **two** candidates and requires a real gap between them — an ambiguous match becomes an honest blank, never a coin-flip guess. |
| **A separate classification pass before extraction** | Extracting fields from a delivery note *as if* it were an invoice is a worse failure than declining it. Asking "is this even a payable?" first, as its own step, keeps that mistake structurally impossible. |
| **Deterministic Python for every calculation, zero AI arithmetic** | Language models are unreliable at exact arithmetic. Every number that reaches `erp.py` was computed by tested, deterministic code from printed facts — the model's only job is *reading*, never *computing*. |
| **pytest with scripted fake clients, not live-API tests** | 82 tests run in under 3 seconds with **zero API key required**, proving the parsing/validation/retry logic in isolation from model variance — a prerequisite for any CI pipeline, and for verifying this submission without burning API quota. |
| **Docker with volume-mounted I/O** | `documents/` and `output/` are mounted, not baked into the image, so the same image runs against any document set without a rebuild. |

---

## Why this architecture beats "PDF → LLM → JSON"

The obvious approach to this problem is: read the fields, fill the record.
That approach books maybe a third of real-world documents and then stalls in
ways that look like a dozen unrelated bugs. The actual problem is not
reading — it's **knowing which facts to trust, and which ones need a second
opinion.**

This system is built around one core principle: **the AI perceives, Python
decides.** A vision-language model is genuinely good at one thing here —
reading a messy, unfamiliar page and telling you what's printed on it. It is
not reliably good at arithmetic, at knowing when two documents that look
related actually aren't, or at knowing when to say "I don't know." So the
system routes each of those needs to whichever side is actually capable of
it:

- **The AI never does math.** Tax-inclusive pricing, SAP-style
  "price-per-N-units" columns, locale-formatted numbers — all read as printed
  facts, then converted deterministically in tested Python. This isn't
  stylistic. A real bug during development proved why it matters: one
  document's printed price applied "per 100 units," and computing
  `quantity × price` the naive way overstated the line by exactly 100x, on
  every single retry, because the AI kept reading the same real number
  correctly — the bug was in trusting raw arithmetic on top of an
  unconverted printed value, not in the AI's perception.

- **The AI never invents a master-data code.** Every resolver — supplier,
  tax, payment term, purchase order, buyer/business-unit — returns a blank
  code rather than a guess whenever confidence is low. This includes a
  specific case worth naming: two of this tenant's own business units share
  the exact same registered address. Address alone cannot tell them apart.
  Fuzzy name-matching is required to use a **confidence margin**, not just a
  threshold — the best match must beat the second-best by a real margin, or
  the match is discarded as ambiguous. A near-tie is not a match.

- **The AI never decides whether its own answer is correct.** That's what
  `erp.py` is for — a deterministic reconciliation engine, provided, never
  modified. Every payable is built, handed to it, and checked against the
  document's own declared total. A mismatch triggers a retry that's told
  the *size* of the gap, never a suggested fix — this keeps the correction
  anchored to real evidence on the page, not reverse-engineered from the
  target number. If retries are exhausted without reconciling, the document
  is **declined**, with the exact gap and attempt count stated — never
  submitted as an unverified figure. An honest "I could not verify this" is
  a correct output for this problem; a confident wrong number is not.

- **Classification happens before extraction, as its own step.** A delivery
  note, purchase order, or statement never has its fields read as if it were
  an invoice, because the system asks "is this even a payable?" before it
  asks "what does it say?"

- **Cost-aware batching.** Every page of every document is rendered and, for
  documents up to a page-count threshold, every page is classified — not a
  fixed 2-3 page sample. Past that threshold, the system falls back to one
  capped, representative sample instead of many full batches. This is a
  deliberate, measured tradeoff: full coverage on a handful of very long
  documents multiplies the number of model calls substantially, and a
  free-tier development key has a hard *daily* token ceiling (confirmed by
  direct observation, not assumption — it was exhausted after only a few
  long documents before this threshold existed). The threshold means ~93% of
  this kit's documents (the 1-2 page majority) get full coverage at
  essentially no extra cost, while the handful of genuinely long documents
  trade some coverage for staying inside a realistic token budget.

---

## Setup

### Prerequisites

- Python 3.11 or newer
- A vision-capable API key from **Groq** (recommended — fast, generous free
  tier) or **Anthropic**
- (Optional) Docker, if you'd rather not manage a local Python environment

### 1. Clone and create a virtual environment

```
git clone <this-repo-url>
cd Zycus_Assessment
python -m venv venv
venv\Scripts\activate        (Windows)
source venv/bin/activate     (macOS/Linux)
```

### 2. Install dependencies

```
pip install -r requirements.txt
```

### 3. Configure credentials

Create a `.env` file in the project root (git-ignored, never committed):

```
GROQ_API_KEY=your_key_here
LLM_PROVIDER=groq
GROQ_MODEL=your_current_vision_model_id
```

The backend is swappable — `LLM_PROVIDER=anthropic` plus
`ANTHROPIC_API_KEY=...` works identically, since nothing in the pipeline
itself knows or cares which provider is active, by design
(`bookable_payable/llm/base.py`).

*A practical note on external providers:* vision-model availability on
free/preview tiers changes over time. If you see a "model not available"
error, check `console.groq.com/playground` for a current vision-capable
model ID and update `GROQ_MODEL` — no code change required.

### 4. Verify the setup (no API key required for this step)

```
python -m pytest tests/ -v
```

If all 82 tests pass, your environment is correctly configured and the
pipeline's logic is verified independently of any live API call — see
[Testing](#testing) below for why this works without a key.

### 5. Add input documents and run

Place PDFs in `documents/`, then:

```
python main.py
```

---

## Running it

```
python main.py
```

Processes every PDF in `documents/`, writes one `output/X.json` per file, and
prints live progress (including rate-limit pauses, so a slow run is visibly
working, not frozen). Optional flags: `--documents-dir`, `--output-dir`.

### With Docker

```
docker build -t bookable-payable .
docker run --rm -e GROQ_API_KEY=your_key -e LLM_PROVIDER=groq ^
  -v "%cd%\documents:/app/documents" -v "%cd%\output:/app/output" ^
  bookable-payable
```
Documents and output are mounted as volumes, not baked into the image, so the
same image runs against any document set without rebuilding.

---

## Testing

```
python -m pytest tests/ -v
```

82 tests, all passing **without any API key**. Every module that itself
calls a vision model (`classify.py`, `extraction.py`) is tested against a
scripted fake client returning canned responses — proving the parsing,
validation, and retry logic in isolation from model variance. Several tests
are built directly from real, hand-verified documents and real model output
rather than synthetic fixtures — including a regression test for the exact
SAP price-unit bug described above, built from the real numbers that exposed
it.

---

## Project structure

```
.
├── main.py                    # the one command: runs the pipeline over documents/
├── erp.py                     # given — the deterministic ERP calculator, never modified
├── AUTODRAFT_SCHEMA.md        # given — the required output shape
├── sample_autodraft.json      # given — a worked example
├── master_data/               # given — supplier/tax/PO/payment-term/company data
├── documents/                 # given — input PDFs (not tracked in git; see .gitignore)
├── bookable_payable/          # our code
│   ├── config.py              # settings: AI backend, retry limits, thresholds
│   ├── ingestion.py           # PDF → page images, batching
│   ├── classify.py            # payable? which type? merged across page batches
│   ├── extraction.py          # header + line-item field extraction
│   ├── masterdata.py          # indexed, margin-based master-data resolution
│   ├── autodraft.py           # deterministic normalization + schema assembly
│   ├── verify.py              # checks a payable against erp.py
│   ├── pipeline.py            # orchestration: classify → merge → extract → verify → retry/decline
│   └── llm/                   # swappable AI backend (Anthropic or Groq)
├── tests/                     # 82 tests, one file per module above
├── Dockerfile
└── DESIGN.md                  # the required write-up: what we learned, what we couldn't solve
```

---

## Known, honestly-documented limitations

- **Long-document page sampling.** Past the batching threshold, coverage is
  a representative sample, not exhaustive — see `DESIGN.md` for which
  documents this affected in the actual run.
- **Development-key rate limits.** A free-tier Groq key imposes both a
  per-minute output-token cap (handled automatically with retry-and-wait)
  and a hard daily token ceiling (handled by failing fast rather than
  uselessly retrying). This is a property of the development credential, not
  the code — the same command with a properly provisioned key processes the
  same way.
- **Multiple payables within one file.** Classification can detect this and
  merges pages into distinct groups accordingly; this was a rare case in
  practice within this kit.

See [`DESIGN.md`](./DESIGN.md) for the full reflection on what this system
understands, how it behaves on an unfamiliar document, and which document
(if any) it concluded could not be solved the way the others were.