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
pip install curl_cffi beautifulsoup4 lxml google-genai pydantic python-dotenv pypdf cryptography
```

## Commands

```bash
python scripts/pull_latest.py    # scrape council, download newest app's PDFs to <slug>/raw/
python scripts/extract.py        # Gemini extracts to <slug>/extracted/{documents,plans}.json
```

## Architecture

```
sava/
├── scripts/
│   ├── pull_latest.py      # scraper (curl_cffi + bs4)
│   └── extract.py          # Gemini extractor
├── <address-slug>/         # one folder per scraped application
│   ├── raw/                # downloaded PDFs (Form 2, Documents, Plans)
│   └── extracted/          # extracted JSON per source PDF
├── docs/                   # pipeline + design notes
│   └── pipeline.md
├── lessons/                # running log of what we've learned
│   └── lessons.md
├── notes/                  # research notes on adjacent tools / prior art
│   └── openconstructionestimate-findings.md
└── .env                    # GEMINI_API_KEY
```

### Naming conventions

| Thing | Pattern | Example |
|---|---|---|
| App folder | `[Address-Slug]/` — verbatim from the council detail-page URL's trailing segment | `9-Admiral-Street-Seddon/` |
| Address slug | Mixed-case, hyphen-separated, as the council emits it — we never normalise it | `9-Admiral-Street-Seddon` |
| Raw PDF | `[Address-Slug]/raw/[source-filename].pdf` — passed through from the council CDN, not renamed | `9-Admiral-Street-Seddon/raw/advertised-plans-tp5120261-9-admiral-street-seddon.pdf` |
| Extracted JSON | `[Address-Slug]/extracted/[type].json` — canonical name per source PDF type, no date, no version | `9-Admiral-Street-Seddon/extracted/plans.json` |
| Doc-type → JSON | `advertised-documents-*.pdf` → `documents.json`; `advertised-plans-*.pdf` → `plans.json` | — |

The `raw/` + `extracted/` split is enforced by both scripts and is the contract that makes adding more apps trivial.