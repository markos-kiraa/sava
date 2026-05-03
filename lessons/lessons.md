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
