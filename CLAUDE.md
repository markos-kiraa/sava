# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

`sava` automates the quoting workflow for a window installation business. The pipeline scrapes Victorian council "advertised planning applications" pages, downloads each application's PDFs, and extracts structured data (practitioner contact, owners, applicant, window list with status and dimensions when available) so the team can produce outbound quotes faster instead of digging through PDFs by hand.

Currently scoped to one council (Maribyrnong, VIC, on the OpenCities CMS) as a single-council demo. Multi-council expansion is deliberately deferred until the demo earns the right to grow — do not add a council registry / abstraction layer prematurely.

## Setup

System dependencies (macOS):

```bash
brew install poppler   # provides pdftotext, pdfinfo, pdftoppm
```

Python (3.11) and packages:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install curl_cffi beautifulsoup4 lxml google-genai pydantic python-dotenv pypdf cryptography "pillow>=10.0" "fpdf2>=2.7"
```

`fonts/DejaVuSans.ttf` and `fonts/DejaVuSans-Bold.ttf` are bundled in the repo so `quote.py`'s PDF renders unicode (em-dash, bullet, ⚠) cleanly via fpdf2.

## Commands

```bash
python scripts/pull_latest.py    # scrape council, download newest app's PDFs to <slug>/raw/
python scripts/extract.py        # Gemini extracts to <slug>/extracted/{documents,plans}.json
python scripts/quote.py <slug>   # renders <slug>/quote/{quote.pdf, email.txt}
```

## Architecture

```
sava/
├── scripts/
│   ├── pull_plans.py        # scraper (curl_cffi + bs4, 8-way parallel ThreadPool)
│   ├── clear.py             # wipes scraped/ for clean re-runs
│   ├── extract.py           # Gemini extractor
│   └── quote.py             # renderer: catalogue + pending-bucket → PDF + email
├── scraped/                 # scraper output (untracked; not in git)
│   └── <state>/<council>/<address-slug>/
│       ├── raw/             # downloaded PDFs (Form 2, Documents, Plans)
│       ├── extracted/       # extracted JSON per source PDF
│       └── quote/           # quote.pdf + email.txt for outbound
├── docs/                    # pipeline + design notes, PRDs
│   ├── pipeline.md
│   └── prds/
│       └── 00-prd-quote-improvements.md
├── lessons/                 # running log of what we've learned
│   └── lessons.md
├── legacy/                  # v1 demo + assets moved out of the active tree
│   ├── archive/9-Admiral-Street-Seddon/   # full v1 single-app demo
│   ├── fonts/               # DejaVuSans TTFs (quote.py needs repoint)
│   ├── images/              # capital-t logos (SAVA_LOGO_PATH needs repoint)
│   ├── notes/               # research notes on adjacent tools
│   └── QN5375.pdf
└── .env                     # GEMINI_API_KEY + SAVA_SENDER_* + SAVA_LOGO_PATH
```

Per-folder specifics live in `<folder>/context.md`.

### Naming conventions

| Thing | Pattern | Example |
|---|---|---|
| State + council | Lowercase, no spaces; state is the AU postal code (`vic`, `nsw`, `qld`, `wa`, `sa`, `tas`, `act`, `nt`); council is the council's short name | `vic/maribyrnong` |
| App folder | `scraped/<state>/<council>/<Address-Slug>/` — slug verbatim from the council detail-page URL's trailing segment | `scraped/vic/maribyrnong/9-Admiral-Street-Seddon/` |
| Address slug | Mixed-case, hyphen-separated, as the council emits it — we never normalise it | `9-Admiral-Street-Seddon` |
| Raw PDF | `<app-folder>/raw/[source-filename].pdf` — passed through from the council CDN, not renamed | `…/9-Admiral-Street-Seddon/raw/advertised-plans-tp5120261-9-admiral-street-seddon.pdf` |
| Extracted JSON | `<app-folder>/extracted/[type].json` — canonical name per source PDF type, no date, no version | `…/9-Admiral-Street-Seddon/extracted/plans.json` |
| Quote output | `<app-folder>/quote/{quote.pdf, email.txt}` — fixed names per app | `…/9-Admiral-Street-Seddon/quote/quote.pdf` |
| Doc-type → JSON | `advertised-documents-*.pdf` → `documents.json`; `advertised-plans-*.pdf` → `plans.json` | — |

The `raw/` + `extracted/` split is the contract that makes adding more apps trivial. `pull_plans.py` writes to this layout today; `extract.py` (`find_app_folders`) and `quote.py` (`_resolve_app_dir`) still glob the repo root and need their globs updated to walk `scraped/<state>/<council>/<slug>/` before consuming `pull_plans.py` output end-to-end.