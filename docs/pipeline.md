## What this is

Document explaining the pipeline of this project. Updated as the project develops.

### Three-stage pipeline

1. **`pull_latest.py`** — lists detail pages on the Maribyrnong listing, HEADs every PDF for `Last-Modified`, picks the application whose newest PDF is most recent, downloads all of that app's PDFs into `<slug>/raw/`. Creates `<slug>/extracted/` empty so the next stage has a home.

2. **`extract.py`** — walks every `<slug>/raw/` folder.
   - `documents.pdf` → whole-PDF Gemini call → `documents.json` (applicant, owners, contacts).
   - `plans.pdf` → hybrid: PyMuPDF reads vector geometry + text per page; Gemini sees each elevation page (4, 5, 8, 9 in the current sample) and returns tight bboxes for every window/door; deterministic measurement of those bboxes against the page's printed scale reference (e.g. `3,130` ceiling height) yields `frame_width_mm`/`frame_height_mm`. Output: `plans.json`.

3. **`quote.py`** — consumes both JSONs.
   - Builds a window schedule (markdown).
   - Prices each item by `area_m² × $/m²` (per-kind rates; defaults for null dims marked `*est`; suspect measurements marked `*VERIFY`).
   - Generates an outbound email body with subject, hook, totals, CTA. Sender details from `.env` (`SAVA_SENDER_NAME` / `_PHONE` / `_EMAIL` / `_COMPANY`).
   - Writes `<slug>/quote/{schedule.md, quote.txt, quote.pdf, email.txt}`.

The three stages are intentionally decoupled — different failure modes (network/Akamai vs Gemini API vs local-only), different costs (free vs paid vs free), different cadences possible.
