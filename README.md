# Zycus AI/ML Assessment — Autodraft Pipeline

Turns a folder of unlabeled supplier documents (invoices, credit memos, delivery
notes, purchase orders — mixed languages, currencies, and layouts) into
structured, ERP-verified payables, following the contract in
`AUTODRAFT_SCHEMA.md`.

## Architecture

```
PDF
 │
 ▼
render ALL pages → page images
 │
 ▼
batch pages (≤3 per call — the vision model's hard per-request limit)
 │  (long documents fall back to one capped, representative sample —
 │   see "Cost-aware batching" below)
 ▼
CLASSIFY each batch
 │  is this payable evidence? what document type? new payable or
 │  continuation of one already seen?
 ▼
MERGE segments into payable groups
 │  pages belonging to the same payable become ONE group, regardless of
 │  which batch they happened to land in; supporting/non-payable pages
 │  are kept out of any group
 ▼
for each payable group:
 │
 ├─► EXTRACT header fields (one call)
 ├─► EXTRACT line items (one call)
 │
 ▼
DETERMINISTIC PYTHON NORMALIZATION
 │  locale-safe numbers, tax-inclusive → net price, SAP-style
 │  "price-per-N-units" → per-unit price — all derived from printed,
 │  grounded facts, never invented
 ▼
MASTER-DATA RESOLUTION
 │  supplier / tax / payment-term / PO / buyer, conservative fuzzy
 │  matching with an ambiguity margin — a near-tie stays blank
 ▼
REPRESENTABILITY GATE
 │  can this document be represented WITHOUT lying about what it says?
 │  (e.g. a printed credited amount with no matching schema field)
 │
 ├── NO ──────────────────────────────────────────► DECLINE
 │
 ▼ YES
ERP VALIDATION (erp.py — never modified)
 │
 ├── MATCH ──────────────────────────────────────► SUBMIT
 │
 └── MISMATCH
      │
      ▼
   retry with the SIZE of the discrepancy as evidence (capped attempts)
      │
      ├── now MATCH ────────────────────────────► SUBMIT
      │
      └── still MISMATCH ─────────────────────────► DECLINE
                                                    (never submit an
                                                     unverified figure)
```

One JSON file per input PDF, always — `{"file", "payables", "declined"}` —
even when something goes wrong partway through.

## Why this architecture, and what makes it different from "PDF → LLM → JSON"

The obvious approach to this problem is: read the fields, fill the record.
That approach books maybe a third of real-world documents and then stalls in
ways that look like a dozen unrelated bugs. The actual problem is not
reading — it's **knowing which facts to trust, and which ones need a second
opinion.**

This system is built around one core principle: **the AI perceives, Python
decides.** A large vision model is genuinely good at one thing here — reading
a messy, unfamiliar page and telling you what's printed on it. It is not
reliably good at arithmetic, at knowing when two documents that look related
actually aren't, or at knowing when to say "I don't know." So the system
routes each of those needs to whichever side is actually capable of it:

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
  Fuzzy name-matching is required to use a **margin**, not just a threshold —
  the best match must beat the second-best by a real margin, or the match is
  discarded as ambiguous. A near-tie is not a match.

- **The AI never decides whether its own answer is correct.** That's what
  `erp.py` is for — a deterministic calculator, provided, never modified.
  Every payable is built, handed to it, and checked against the document's
  own declared total. A mismatch triggers a retry that's told the *size* of
  the gap, never a suggested fix — this keeps the correction anchored to
  real evidence on the page, not reverse-engineered from the target number.
  If retries are exhausted without reconciling, the document is **declined**,
  with the exact gap and attempt count stated — never submitted as an
  unverified figure. An honest "I could not verify this" is a correct output
  for this problem; a confident wrong number is not.

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
├── tests/                     # 84 tests, one file per module above
├── Dockerfile
└── DESIGN.md                  # the required write-up: what we learned, what we couldn't solve
```

## Setup

1. **Python 3.11+**, virtual environment:
   ```
   python -m venv venv
   venv\Scripts\activate        (Windows)
   source venv/bin/activate     (macOS/Linux)
   pip install -r requirements.txt
   ```

2. **API key** — create `.env` in the project root (git-ignored, never committed):
   ```
   GROQ_API_KEY=your_key_here
   LLM_PROVIDER=groq
   GROQ_MODEL=your_current_vision_model_id
   ```
   The backend is swappable (`LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY=...`
   works identically) — nothing in the pipeline itself knows or cares which
   provider is active, by design (`bookable_payable/llm/base.py`).

   *A practical note on external providers:* vision-model availability on
   free/preview tiers changes over time. If you see a "model not available"
   error, check `console.groq.com/playground` for a current vision-capable
   model ID and update `GROQ_MODEL` — no code change required.

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

## Testing

```
python -m pytest tests/ -v
```

84 tests, all passing without any API key. Every module that itself calls a
vision model (`classify.py`, `extraction.py`) is tested against a scripted
fake client returning canned responses — proving the parsing, validation, and
retry logic in isolation from model variance. Several tests are built
directly from real, hand-verified documents and real model output rather
than synthetic fixtures — including a regression test for the exact SAP
price-unit bug described above, built from the real numbers that exposed it.

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

See `DESIGN.md` for the full reflection on what this system understands,
how it behaves on an unfamiliar document, and which document (if any) it
concluded could not be solved the way the others were.