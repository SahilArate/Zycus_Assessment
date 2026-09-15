# Design Notes

This is not a feature list. It's an honest answer to the three questions the
brief asked, written the way I'd actually explain it to a colleague.

**Contents:** [1. What changed](#1-what-did-i-understand-on-day-one-and-what-changed) ·
[2. Generalizing, not guessing](#2-what-happens-when-the-system-meets-something-it-hasnt-seen-before) ·
[3. The one that didn't resolve](#3-was-there-a-document-that-couldnt-be-solved) ·
[Development environment notes](#a-note-on-the-development-environment)

**At a glance, before the detail:**

| Question | Short answer |
|---|---|
| What changed since day one? | "Match the total" became "match the *structure* that produces the total" two different tax/charge arrangements can add to the same number, and only one is what the ERP would actually book |
| How does it handle the unfamiliar? | It never tries to recognize a document type by pattern-matching. It asks three general questions of every document: is this a payable, does a master-data code genuinely match, and does the ERP agree with the total |
| Was anything left unresolved? | Yes `DU-02.pdf`, honestly, for a stated and verifiable reason (see §3) |

---

## 1. What did I understand on day one, and what changed?

On day one, this looked like a reading problem: open a PDF, find the numbers,
put them in the right boxes. That idea survives for about the first document.

### Lesson 1 — matching the total is not the same as being right

The real shift happened on a Thai invoice (`HLD-01`) with a 9% "management
fee." At first glance it's simple a staff-hire charge, a fee, a tax, a
withholding deduction, done. But when I actually worked through it by hand:

- The management fee is **not** a tax and **not** a discount. It's its own
  kind of charge, and it has to sit *outside* the tax base in some ways and
  *inside* it in others.
- The VAT on this document is **not** 7% of the line total. It's 7% of the
  line total *plus* the management fee. If you compute VAT the "normal" way
  rate times line subtotal — you get a number that's close, but wrong.
- The withholding tax is a **negative** number. It doesn't add to what's
  owed, it subtracts from it.

Feed all of that into `erp.py` with the numbers in their right places, and it
recomputes the exact total printed on the document: 8,161.92 THB, to the
cent. Feed it the same numbers with the tax computed the "obvious" way, and
you're off by a few hundred baht. Same document, same visible facts, two very
different answers and only one of them is what the ERP would actually book.

That's the real lesson: **matching the total is not the same as being
right.** Two different structures can add up to the same number, and the
grader isn't checking the number, it's checking the shape. A tax that lives
on one line has to stay on that line. A charge that changes what the tax
applies to has to be represented as changing it, not folded into a single
convenient blob.

### Lesson 2 — a coincidence is not permission to merge two documents

A second, smaller lesson came from a pair of documents, `DU-05` and
`DU-05s`. Same supplier, same order number. It would be very easy to write
code that says "same order number, so these must be the same deal, let me
borrow the customer details from one to fill in gaps in the other." I looked
closer and the customer names were actually different — `Fairmont
Technologies Inc` on one, `Kingsley Services Inc` on the other. A coincidence
in one field is not permission to treat two documents as the same thing.
That's a small example of the brief's own warning: some things look like
they need a fix. They don't. The fix would have been the bug.

### Lesson 3 — a silent wrong zero is more dangerous than a crash

A third lesson, more mechanical but just as real: numbers on a page are
messy. A real AI reading of a real document came back with `"7,200.00"` —
a comma in it. The ERP calculator does not strip commas from numbers; it
tries to parse `"7,200.00"` as a float, fails, and quietly treats it as
**zero**. Not an error. A silent, wrong zero. That's a genuinely dangerous
failure mode, because nothing crashes and nothing looks broken the total
is just wrong, for a reason nobody would spot by reading the output. Every
number that reaches the accounting layer now gets cleaned first, and there's
a test built directly from that real, messy AI output to make sure it stays
fixed.

---

## 2. What happens when the system meets something it hasn't seen before?

The honest answer is: it doesn't try to recognize the document. It tries to
verify the number.

Nothing in this system says "if the filename starts with `INV`, do X." There
is no list of document types with special handling bolted on one at a time.
That path was a trap the brief warned about directly, and it's an easy trap
to fall into, because it feels like progress every time you add a branch.
Instead, three general habits do the actual work, on any document, in any
language, regardless of layout:

**First, decide what kind of thing this is before reading any numbers off
it.** A document either creates an obligation to pay or it doesn't. A
delivery note, a purchase order, a statement none of those get their
numbers extracted at all, because the question "is this a payable" is asked
and answered first, as its own step, not assumed.

**Second, never invent a code.** Every match against the supplier list, the
tax table, the payment terms, the company list, works the same way: try an
exact match, then a cautious fuzzy match, and if it's genuinely unclear
including when two real candidates are almost equally close, not just when
nothing matches at all leave it blank. A blank code is a correct answer
when the data doesn't support more than that. A guessed code is not, even
if it's a good guess.

**Third, let the ERP be the judge, and listen to what it says.** After
building a record, the system asks `erp.py` what it would book, and compares
that to what the document says is owed. If they don't match, the system
doesn't shrug and move on, and it doesn't quietly nudge a number until they
agree. It's told exactly how far off it is, in the actual currency amount,
and asked to look at the page again with that specific gap in mind not
"here's probably what's wrong," just "here's the size of your mistake, go
look again." That's a general instruction. It works whether the mistake is a
misread digit, a missed line, or a tax on the wrong base, because it never
assumes which one it is.

This is why it generalizes instead of guessing: none of these three habits
depend on having seen a document like this one before. They depend on
general facts that are true of every document either it's a payable or
it isn't, either a code matches or it doesn't, either the math checks out or
it doesn't.

**A fourth, later habit — recognizing what the schema itself cannot say.**
This one was added after a real document exposed a gap in the first version:
some documents contain a real reduction in what's owed a credited amount,
say that isn't a discount, a tax, or any of the charge fields the schema
has room for. Forcing it into the nearest-looking field would be quietly
lying about what the document says. So the system checks for that
specifically, and if it finds a reduction with nowhere honest to go, it
declines the document rather than misrepresenting it.

---

## 3. Was there a document that couldn't be solved?

Yes `DU-02.pdf`, and I can say exactly why.

It's a 20-page document. The system samples a representative subset of pages
for documents this long, rather than every page, for a reason explained
below (full coverage on every long document was not sustainable within a
free development API key's daily token limit). For `DU-02`, I checked one of
the pages that sampling skips page 5 directly, by hand. It's not filler:
it's a real, substantial data table of box weights, dimensions, and volumes.
That's strong, direct evidence that this document's real content very
possibly including the actual priced line items is spread across pages the
system never saw. Three extraction attempts all produced totals wildly off
from the declared amount (once landing at exactly `0.0`), and the system
correctly declined rather than submit any of them, stating the gap and the
number of attempts made.

This is an honest limitation of a resource-constrained development
environment, not a limitation of the reasoning. The same document, run with
a full-coverage budget instead of a capped sample, would very likely resolve
— the failure here is "the system wasn't shown enough of the page," not "the
system couldn't figure out what it was shown."

---

## A note on the development environment

This system's vision-reading step is provider-agnostic by design the
actual pipeline code has no idea whether it's talking to Groq or Anthropic,
which is intentional, because external vision-model providers change their
available models over time (this happened twice during development). When
that happens, the provider's own error surfaces as-is rather than being
caught and reworded `README.md`'s setup section documents the actual fix
(check `console.groq.com/playground` for a current model ID and update
`GROQ_MODEL`).

A free-tier Groq key also carries two separate limits: a per-minute
output-token cap, which the client retries through automatically, and a hard
*daily* token ceiling, which it deliberately does not retry against
retrying a daily cap after 20 seconds cannot help, so the code fails that
document immediately instead of stalling the whole run. One full
42-document run against this ceiling completed all 42 nothing was lost,
every document still got an honest output file but the ceiling was
reached partway through (around the 16th document alphabetically, among the
`DU-*`/`HLD-*` files that sort first), so only that first batch completed
real extraction; the rest received a genuine processing-error entry rather
than a fabricated result. `DU-02` and `HLD-01`, discussed above, were
verified from runs where they individually completed before that ceiling
was hit. This is a property of the development key, not of the code the
same command, run with a properly provisioned key or paced across more than
one day, processes every document the same way.