## What this is

Document explaining the pipeline of this project. This file will be updated as the project develops.

### Two-stage pipeline

1. **`pull_latest.py`** — lists every detail page on the Maribyrnong listing, HEADs every PDF for `Last-Modified`, picks the application whose newest PDF is most recent, downloads all of that app's PDFs into `<slug>/raw/`. Creates `<slug>/extracted/` empty so the next stage has a home.

2. **`extract.py`** — walks every `<slug>/raw/` folder. For each, sends each known PDF type to Gemini 2.5 Pro with a Pydantic schema bound to `response_mime_type=application/json` + `response_schema=...`. Whole-PDF inline calls (the per-page high-DPI render path was tried and regressed accuracy on small contact text — see "lessons learned" below).

The two stages are intentionally decoupled — different failure modes (network/Akamai vs Gemini API), different costs (free vs paid), different cadences possible.

A `quote.py` step that consumes both JSONs and prints a terminal-formatted draft quote is the next piece of work; it has not been built yet.