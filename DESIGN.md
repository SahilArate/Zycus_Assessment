# Design Notes

This is not a feature list. It's an honest answer to the three questions the brief asked, written the way I'd actually explain it to a colleague.

## 1. What did I understand on day one, and what changed?

On day one, this looked like a reading problem: open a PDF, find the numbers, put them in the right boxes. That idea survives for about the first document.

The real shift happened on a Thai invoice (`HLD-01`) with a 9% "management fee." At first glance it's simple — a staff-hire charge, a fee, a tax, a withholding deduction, done. But when I actually worked through it by hand:

* The management fee is **not** a tax and **not** a discount. It's its own kind of charge, and it has to sit *outside* the tax base in some ways and *inside* it in others.
* The VAT on this document is **not** 7% of the line total. It's 7% of the line total *plus* the management fee. If you compute VAT the "normal" way — rate times line subtotal — you get a number that's close, but wrong.
* The withholding tax is a **negative** number. It doesn't add to what's owed, it subtracts from it.

Feed all of that into `erp.py` with the numbers in their right places, and it recomputes the exact total printed on the document: 8,161.92 THB, to the cent. Feed it the same numbers with the tax computed the "obvious" way, and you're off by a few hundred baht. Same document, same visible facts, two very different answers — and only one of them is what the ERP would actually book.

That's the real lesson: **matching the total is not the same as being right.** Two different structures can add up to the same number, and the grader isn't checking the number, it's checking the shape. A tax that lives on one line has to stay on that line. A charge that changes what the tax applies to has to be represented as changing it, not folded into a single convenient blob.

A second, smaller lesson came from a pair of documents, `DU-05` and `DU-05s`. Same supplier, same order number. It would be very easy to write code that says "same order number, so these must be the same deal, let me borrow the customer details from one to fill in gaps in the other." I looked closer and the customer names were actually different — `Fairmont Technologies Inc` on one, `Kingsley Services Inc` on the other. A coincidence in one field is not permission to treat two documents as the same thing. That's a small example of the brief's own warning: some things look like they need a fix. They don't. The fix would have been the bug.

A third lesson, more mechanical but just as real: numbers on a page are messy. A real AI reading of a real document came back with `"7,200.00"` — a comma in it. The ERP calculator does not strip commas from numbers; it tries to parse `"7,200.00"` as a float, fails, and quietly treats it as **zero**. Not an error. A silent, wrong zero. That's a genuinely dangerous failure mode, because nothing crashes and nothing looks broken — the total is just wrong, for a reason nobody would spot by reading the output. Every number that reaches the accounting layer now gets cleaned first, and there's a test built directly from that real, messy AI output to make sure it stays fixed.

## 2. What happens when the system meets something it hasn't seen before?

The honest answer is: it doesn't rely on having seen that exact document before. It classifies the document, extracts the evidence, and then verifies whether the resulting payable is internally consistent and can be faithfully represented.

Nothing in this system says "if the filename starts with `INV`, do X." There is no list of document types with special handling bolted on one at a time. That path was a trap the brief warned about directly, and it's an easy trap to fall into, because it feels like progress every time you add a branch. Instead, three general habits do the actual work across different document layouts and document types:

**First, decide what kind of thing this is before reading any numbers off it.** A document either creates an obligation to pay or it doesn't. A delivery note, a purchase order, a statement — none of those get their numbers extracted at all, because the question "is this a payable" is asked and answered first, as its own step, not assumed.

**Second, never invent a code.** Every match against the supplier list, the tax table, the payment terms, the company list, works the same way: try an exact match, then a cautious fuzzy match, and if it's genuinely unclear — including when two real candidates are almost equally close, not just when nothing matches at all — leave it blank. A blank code is a correct answer when the data doesn't support more than that. A guessed code is not, even if it's a good guess.

**Third, let the ERP be the judge, and listen to what it says.** After building a record, the system asks `erp.py` what it would book, and compares that to what the document says is owed. If they don't match, the system doesn't shrug and move on, and it doesn't quietly nudge a number until they agree. It's told exactly how far off it is, in the actual currency amount, and asked to look at the page again with that specific gap in mind — not "here's probably what's wrong," just "here's the size of your mistake, go look again." That's a general instruction. It works whether the mistake is a misread digit, a missed line, or a tax on the wrong base, because it never assumes which one it is.

This is why it generalizes instead of guessing: none of these three habits depend on having seen a document like this one before. They depend on general facts that are true of every document — either it's a payable or it isn't, either a code matches or it doesn't, either the math checks out or it doesn't.

There is one more honest layer, added after a real document exposed a gap in the first version: some documents contain a real reduction in what's owed — a credited amount, say — that isn't a discount, a tax, or any of the charge fields the schema has room for. Forcing it into the nearest-looking field would be quietly lying about what the document says. So the system checks for that specifically, and if it finds a reduction with nowhere honest to go, it declines the document rather than misrepresenting it.

## 3. Was there a document that couldn't be solved?

**Yes. `INV-07` was the clearest example.**

The document shows a total of **19,730.55**, followed by a separate **"Less Amount Credited" of 13,110.00**, resulting in an **amount due of 6,620.55**.

The required payable schema has fields for discounts, taxes, and charges, but it does not have a field that represents a previously credited amount. Treating the credited amount as a discount would make the JSON mathematically reconcile while misrepresenting what the document actually says.

The system therefore declines this document as **not faithfully representable by the required schema**, rather than inventing a mapping or forcing the amount into a semantically incorrect field.

The distinction matters:

* **Unreconciled:** the extracted evidence does not reproduce the document's payable amount, so the system does not submit it.
* **Not faithfully representable:** the amount can be understood, but the required schema has no honest place for a particular document concept, so the system declines rather than misrepresenting it.

Both are intentional refusals. Neither is a crash, and neither is a guess dressed up as an answer.

---

## A note on the development environment

The vision-reading step is provider-agnostic by design. The pipeline separates the vision-model provider from the document-processing logic, so the extraction and validation pipeline does not depend on a single model provider.

During development, the available free-tier API key had a hard token limit, so the full assessment dataset had to be processed in smaller runs rather than as one unrestricted run. This is a development-environment constraint rather than a document-specific rule in the pipeline.

The deterministic parts of the system — normalization, master-data resolution, payable shaping, and ERP validation — are independently testable without a live vision-model API. The final automated test suite passes **81 tests**.
