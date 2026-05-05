## Lessons learned (load-bearing)

These came from real attempts in this repo. Reverting to any of them needs a real reason.

### Akamai TLS fingerprinting on the council site

The Maribyrnong site sits behind Akamai which JA3-fingerprints clients. Plain `requests` returns 403; `curl_cffi` with `impersonate="chrome131"` works. **`chrome124` is also blocked** — it's been specifically flagged. If updating curl_cffi versions, verify the chosen `impersonate` profile still gets through before assuming nothing changed.

### `pdftotext` cannot read scanned form pages

The `advertised-documents-*.pdf` files include the planning-permit application form as scanned/imaged pages (typically pages 1–2). `pdftotext` returns nothing useful from them. The applicant's phone and email — the highest-value contact for outbound — live exclusively on those imaged pages. **Gemini's vision is what gets them.** A regex-only extractor was prototyped, missed the applicant entirely, and was abandoned. Don't reinstate it without OCR'ing the form pages first.

### Window dimensions are often genuinely absent

Many residential plans (including the example in this repo) have no window schedule sheet and no per-window dimension labels on elevations. Gemini will (and should) return `null` for `approx_width_mm` / `approx_height_mm` when there's nothing labelled. The prompt explicitly forbids inventing "typical" sizes — earlier runs hallucinated 600/900/1200/1500/1800 mm because those are standard Australian window sizes, not because they were on the drawing. If a draftsperson included a schedule, Gemini reads it; otherwise expect nulls and flat-rate the quote.

### High-DPI page rendering did not help

Rendering specific pages at 350 DPI and sending them as PNG `Part`s (instead of inline PDF) made Gemini *less* accurate on cover-page contact details — small text was hallucinated rather than read. The whole-PDF inline approach is what the script uses, and is what works.

### Apify `ntriqpro/blueprint-intelligence` is a dead end

Tested empirically. The Actor is a thin proxy to a single-author Cloudflare tunnel (`ai.ntriq.co.kr`) that's offline ~13% of the time historically, and its output schema aggregates elements by type (`{elementType, count, details}`) — there's no per-element dimension pairing, so it can't produce a window schedule even when working. Don't reconsider.

### Archicad-exported PDFs preserve vector geometry — measure deterministically

Town-planning sets don't print per-window dimensions, but the source PDFs (Archicad → PDFTron) keep all text + line segments as vectors. Pipeline that works: PyMuPDF reads dimension labels (e.g. `3,130`) and matches them to nearby dimension lines to derive mm-per-point per page; Gemini returns a tight bbox per window on each rendered elevation; we filter line segments inside the bbox (5% pad, midpoint-inside, length ≤ 1.2 × bbox max-dim) and take their bounding rect. Empirical accuracy ±5–20 mm. Beats any LLM-direct measurement.

### No defaults — pending bucket instead

The first cut of `quote.py` filled missing dimensions from a per-kind default table (`window: 1200×1500`, `door: 2400×2100`) and tagged the row `*est`. The PDF still printed a price; the `*est` flag was easy to miss in a row of similar-looking lines. In effect a typical Australian window size — never measured, never on the drawing — was masquerading as a measurement and being multiplied by the rate to produce a confident-looking subtotal. Same problem on the suspect-measurement path: out-of-range dims got swapped for the default, again silently priced. This is the same hallucinated-typical-sizes failure mode flagged in the dimensions-often-absent lesson, just one layer down the pipeline.

The replacement is a pending bucket. Items with missing dims, out-of-range dims, implausible aspect ratio, unknown type, or no catalogue entry are routed out of the priced totals entirely, render with a `PENDING SITE MEASURE` badge and the failing reason(s), and never contribute a manufactured number to the subtotal. The PDF leans into the audit narrative ("priced 1 of 6, pending 5 — locks at site measure") instead of fabricating a complete price book from incomplete inputs. If a future regression brings defaults back, it has to justify why guessing a dimension is now safer than admitting the drawing didn't show one.

### Per-elevation Gemini calls lose cross-sheet status context

Switching plans extraction from one whole-PDF call to per-elevation calls gave us tight bboxes (and therefore measurable dimensions) but lost the cross-sheet comparison that previously identified `demolished` items. The new flow correctly tags `existing` and `new` per page but can't see "this exists today and isn't on the proposed sheet → demolished." Acceptable for the demo; revisit if scope-of-removal pricing matters.
