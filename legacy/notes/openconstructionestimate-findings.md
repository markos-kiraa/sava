# OpenConstructionEstimate (DDC CWICR) — what's useful for sava

Repo: https://github.com/datadrivenconstruction/OpenConstructionEstimate-DDC-CWICR
Cloned to: `~/Documents/OpenConstructionEstimate-DDC-CWICR`

## What this repo actually is

DDC CWICR (Construction Work Items, Components & Resources) is a **multi-region construction cost database** — 55,719 work items, 27,672 resources, 30 country tracks, with **pre-computed OpenAI embeddings** (`text-embedding-3-large`, 3072-dim) ready for Qdrant. Around it sits a set of n8n workflows and Python reference scripts that turn user input (text / photo / PDF / BIM model) into priced cost estimates by vector-matching against the database.

It does NOT contain architectural drawing parsers, window-dimension extractors, or per-window-from-elevation tooling. It WILL NOT solve our "no dimensions on the elevation" problem. What it can do is replace our `$1500-per-window` stub with real Sydney-AUD pricing once we *have* a window list.

## High-value items for sava

### 1. Sydney / AUD priced catalog — pricing reference

| | |
|---|---|
| **Path** | `AU___DDC_CWICR/DDC_CWICR_AU_SYDNEY_Catalog.csv` (also `.xlsx`, `.parquet`) |
| **What** | 7,185 construction resources with prices in AUD. 95 window-related items: aluminium window blocks, plastic window blocks, steel window structures, aluminium stained-glass windows, window sills, glazing materials, install equipment (vacuum grippers etc.) |
| **Sava use** | Replace the per-window flat rate with looked-up Sydney unit costs for the matched window type. e.g. `KADX-ME-KAME-KAME` ("Aluminium alloy stained glass windows with flashings and drains") is `620 AUD/m²`. Multiply by `width × height` and we have a real per-window price instead of a stub. |
| **Score** | **High.** Sydney isn't Maribyrnong but it's the closest market in the dataset (and the cheapest path to AUD pricing). The schema is documented in `DATA_DICTIONARY.md`. |

### 2. Pre-computed Sydney embeddings — semantic match-by-description

| | |
|---|---|
| **Path** | `AU___DDC_CWICR/AU_SYDNEY_workitems_costs_resources_EMBEDDINGS_3072_DDC_CWICR.snapshot` |
| **What** | A Qdrant snapshot containing OpenAI 3072-dim vectors of every Sydney work item. Restore once with `qdrant-client snapshot upload`, then query at runtime. |
| **Sava use** | Best way to match free-text Gemini output ("Side-facing aluminium window in Bed 1") to the right catalog item without writing brittle regex. Cost: only the per-query embedding (≈$0 per quote). |
| **Score** | **High** if we want anything better than substring matching. Adds an OpenAI key + a local Qdrant container as runtime dependencies. |

### 3. `estimate_from_photo.py` — reference implementation

| | |
|---|---|
| **Path** | `0_Workflow and Pipelines CWICR/python/04-cost-estimation-photo/estimate_from_photo.py` (171 lines) |
| **What** | End-to-end script: vision LLM extracts elements (description + qty + unit) → embed each description → Qdrant search → multiply qty × unit_cost → print priced BOQ. Uses GPT-4o + OpenAI embeddings + Qdrant. |
| **Sava use** | Direct template for our quote step. Our shape is **identical** — Gemini already gives us a `windows[]` list with description + dimensions, exactly the input this script needs. The only swap is: we already have the window list (don't need a separate vision call), and we'd point at the AU collection (`ddc_au_sydney`) instead of Toronto. |
| **Score** | **High.** Cleanest single-file recipe in the repo. Read it once, then port the search-and-price half. |

### 4. `generate_boq.py` — output-format template

| | |
|---|---|
| **Path** | `0_Workflow and Pipelines CWICR/python/05-boq-generation/generate_boq.py` (151 lines) |
| **What** | Reads CSV of `(description, quantity, unit)`, embeds + searches Qdrant per row, exports priced Excel BOQ with rate code, matched description, unit, qty, rate, amount, total. |
| **Sava use** | When the eventual `quote.py` outputs more than terminal text — e.g. emailable PDF/Excel quote — the column structure here is industry-standard and the right shape to mimic. Don't reinvent. |
| **Score** | **Medium.** Useful as a structural cheat-sheet, not as code to import. |

### 5. CAD/BIM 10-stage pipeline guide — methodology

| | |
|---|---|
| **Path** | `0_Workflow and Pipelines CWICR/n8n-guides/workflow-4-cad-bim-pipeline.md` |
| **What** | Documentation for a 10-stage pipeline that decomposes BIM elements (e.g. "concrete wall") into multiple work items ("formwork install" + "rebar place" + "concrete pour" + "formwork remove" + "surface finish"), each priced separately, then aggregated by phase. Includes a validation stage (Stage 7.5) for sanity-checking outputs. |
| **Sava use** | Tells us how a *real* quote evolves from `count × rate` toward `decomposed line items per window`. Each window isn't one cost — it's frame supply + frame install + glazing + glazing install + flashings + reveals + paint. Worth reading **before** we expand the schema beyond a single price-per-window. |
| **Score** | **Medium** for the demo. **High** when we move beyond stub pricing. |

### 6. Resource-Based Costing methodology

| | |
|---|---|
| **Path** | `1_AI_INSTRUCTIONS/INSTRUCTIONS.md`, `DATA_DICTIONARY.md` |
| **What** | The dataset separates `Norm × Price`. Norms (labour hours, machine hours, resource quantities) are constant across regions; only prices and translatable text vary by track. |
| **Sava use** | When sava grows beyond Maribyrnong, this is the model: don't store per-region quotes — store the *takeoff* (window dimensions + types) and apply a regional cost layer at quote time. Cheap to swap regions, cheap to re-quote when prices move. |
| **Score** | **Medium.** Architectural guidance, not code. |

### 7. Catalog schema — borrowable column names

| | |
|---|---|
| **Path** | `DATA_DICTIONARY.md` |
| **What** | Documented 93-column work-items schema and 18-column catalog schema with industry-standard classification (DIN 276, MasterFormat, UniFormat, OmniClass, NRM). |
| **Sava use** | When our `Window` model grows to include type/material/glazing, borrow their field names (`rate_unit_of_measure`, `total_cost_per_position`, `material_*`, `labor_*`) for compatibility with anything downstream that consumes our data. |
| **Score** | **Low–medium.** Reference material, not urgent. |

## Looks promising, isn't

- **`workflow-2-photo-estimate.md`** — same flow as `estimate_from_photo.py` but for n8n. Skip the n8n workflow JSON; the Python script is cleaner.
- **The non-AU language directories** (DE, EN, ES, FR, etc.) — wrong currency for sava. Don't read them.
- **Telegram bot workflows (1 & 3)** — not relevant to our pipeline shape.
- **`DataDrivenConstruction_Book_2ndEdition_ArtemBoiko_2025_en-UK.pdf`** — marketing/methodology book bundled in `AU___DDC_CWICR/`. Skim only if curious about BIM-takeoff theory.
- **`1_AI_INSTRUCTIONS/CLAUDE.md`, `OPENCODE.md`, `ANTIGRAVITY.md`** — instructions for *AI assistants using THIS repo*, not prompt templates we'd reuse for Gemini.
- **The 19 derived-track builder (`11-country-track-builder/add_country_track.py`)** — only useful if sava ever wants to ship its OWN regional database, which is well past demo scope.

## Honest caveats

1. **Sydney ≠ Melbourne.** The AU track is Sydney-priced; Maribyrnong is metropolitan Melbourne. Material prices will be close but not identical; labour rates differ. Acceptable for a demo, NOT defensible against a competing real quote.
2. **Catalog is resource-level, not finished-install-level.** A row like "Aluminium alloy window blocks: 511 AUD/t" is the *material*. The full installed cost includes labour + equipment + transport + margin. The work-items Parquet (`AU_SYDNEY_workitems_costs_resources_DDC_CWICR.parquet`) bundles those into installed rates — that's what we should match against, not the catalog CSV.
3. **CC BY 4.0 attribution required.** Trivial to comply with; one line in the quote footer or repo README.

## Recommended adoption path for sava

Three steps in order. Stop at any point that's "enough" for the demo.

1. **CSV-only pricing (1 hr).**
   Load `AU___DDC_CWICR/DDC_CWICR_AU_SYDNEY_Catalog.csv` with pandas. For each window in `plans.json`, do a simple `str.contains("window")` filter + pick a heuristic match (e.g. always use `KADX-ME-KAME-KAME` aluminium-window-with-flashings @ 620 AUD/m² for new windows). Multiply by `width × height` (or use a default 1.2 m × 1.5 m if dimensions are null). Replace the `$1500` stub.

2. **Qdrant + embeddings (1 day).**
   Stand up Qdrant in Docker, restore the AU snapshot, add OpenAI key. For each window, embed its description+notes string and search the AU collection. Use top-1 match's `total_cost_per_position`. Now matches are semantic, not heuristic. Adapt `estimate_from_photo.py` lines 88–97 — that's the exact shape.

3. **Decomposition (1+ week).**
   Adopt the workflow-4 pattern. Each window becomes ≥3 line items (frame supply, glazing, install). Each is searched separately. Sum per window. Add Stage-7.5-style validation. This is when the quote stops being a stub and starts being defensible.

Stop at step 1 for the demo unless step 2 is genuinely cheap.
