# PRD — Quote Improvements (v1)

Demo-grade rewrite of `scripts/quote.py` to bring the outbound artifact
close to a real installer's quote (using KB Windows' QN5375 as the
reference) without compromising the deterministic-measurement principle
of the pipeline.

Scope: changes are confined to `scripts/quote.py`, `.env`, and the
existing logo asset. No changes to `scripts/extract.py` or
`scripts/pull_latest.py`.

---

## 0. Read these first

A fresh implementer should read, in order:

- [CLAUDE.md](../CLAUDE.md) — project setup, naming conventions, current pipeline.
- [scripts/quote.py](../scripts/quote.py) — the file being rewritten.
- [scripts/extract.py](../scripts/extract.py) — **Pydantic schemas only; do not modify this file.** `Window`, `Project`, `Practitioner`, `DocumentsExtraction`, `PlansExtraction` are the contract `quote.py` reads.
- [9-Admiral-Street-Seddon/extracted/plans.json](../9-Admiral-Street-Seddon/extracted/plans.json) — golden input for the demo.
- [9-Admiral-Street-Seddon/extracted/documents.json](../9-Admiral-Street-Seddon/extracted/documents.json) — golden input for the demo.
- [docs/pipeline.md](pipeline.md) — current behaviour (will be updated as part of this work).
- [lessons/lessons.md](../lessons/lessons.md) — load-bearing constraints (especially the no-default-sizes lesson).

Venv setup additions for this work:

```bash
pip install "pillow>=10.0" "fpdf2>=2.7"
```

The schema definitions in `extract.py` are unchanged. The pending-bucket logic lives in `quote.py` only — it is computed at quote-render time, not stored back to `plans.json`.

---

## 1. Goals

1. The outbound PDF reads like an industry quote, not a debug dump.
2. The pipeline never invents data. Items with missing or implausible
   inputs are flagged "pending site measure" and excluded from totals.
3. The artifact is a credible single-council demo on the 9 Admiral
   Street sample, replicating QN5375's structure with Capital T
   Partners' branding.

## 2. Success criteria (9 Admiral demo)

After running `python scripts/quote.py 9-Admiral-Street-Seddon`:

- Exactly two files emitted under `9-Admiral-Street-Seddon/quote/`:
  `quote.pdf` and `email.txt`. (`schedule.md` and `quote.txt` no
  longer produced.)
- After the renovation-scope filter, exactly **6 items** remain
  (existing plans.json items 11, 12, 13, 14, 15, 17).
- Exactly **1 priced** (item 15 — awning 1204×1707) and **5 pending**
  (items 11, 13, 14, 17 missing dims; item 12 fails plausibility).
- Indicative subtotal ≈ $2,260 + GST → total ≈ $2,490 (priced item only).
- PDF page 1 shows: Capital T logo, brand block, customer block,
  quote header (number, dates, sales person), and the first per-item
  blocks.
- Each per-item block carries: `W{nn}` or `D{nn}` label, framing,
  dimensions (or `—`), finish, glass, hardware, reveals, screen,
  frame weight (or `—`), wind specs, energy values, and a small
  outer-rectangle frame thumbnail.
- Pending items render with `—` in dimension/weight slots and a
  visible **PENDING SITE MEASURE** badge listing the reason(s).
- Last page is the adapted Terms & Conditions, beginning with the
  "indicative quotation" preamble.
- `email.txt` matches the audit-narrative template with the priced /
  pending counts merged in.

---

## 3. In scope

| # | Change | Source decision |
|---|---|---|
| 1 | Filter `windows` to `status in {"new", "altered"}` before pricing | Q1 |
| 2 | Inline `CATALOGUE` dict in `quote.py` keyed by `(kind, type)` with 12 entries (see §A1) | Q2, Q3 |
| 3 | Generate per-item simple outer-rectangle frame thumbnail in PDF | Q2 |
| 4 | Sequential `W01`, `W02`, …, `D01`, `D02`, … item labels (numbered per kind, no room) | Q4 |
| 5 | Drop per-line `$/m²` and `AUD` columns from PDF; show frame weight (kg) instead | Q5 |
| 6 | Delete `DEFAULT_SIZE_MM`, `PLAUSIBLE_MAX_MM`, `_resolve_size`. Replace `_is_suspect` with `_review_reasons`. Items with any review reason go to a pending bucket and are not priced | Q6 |
| 7 | Drop `render_schedule` and `render_quote` (text). Stop writing `.md` and `.txt` outputs | Q7 |
| 8 | Quote number `QN-{first two slug segments}-V1` (e.g. `QN-9-ADMIRAL-V1` from slug `9-Admiral-Street-Seddon`); valid-until = today + 30 days; sales person = `SAVA_SENDER_NAME`; render full sender block from `.env` | Q8 |
| 9 | Add `pillow` to dependency list to embed `capital-t-logo.webp` natively in `fpdf2` | Logo decision |
| 10 | Adapt KB's page-37 Terms & Conditions to Capital T Partners; prepend the indicative-quotation preamble | Terms Q9 |
| 11 | Rewrite email body to lead with the audit narrative (priced / pending counts) instead of a confident total | Email Q13 |
| 12 | Render header `Capital T Partners Pty Ltd` (no separate trading name), with full ABN/ACN/address footer and 10% GST line preserved (entity is GST-registered) | Capital T finding |

## 4. Out of scope (deferred)

- Multi-dwelling unit grouping (`Unit 1`, `Unit 2`, …)
- Real quote version tracking across re-issues — always emits `V1`
- Pane-split frame thumbnails (e.g. `803|803|803`) — needs new
  extraction; thumbnail is outer rectangle only
- Room-aware labels (`W01-Entry`, `W02-Living`)
- Floor-plan-aided extraction
- Demolished-item recovery (cross-sheet reconciliation)
- Per-item glass / hardware / finish overrides
- Variable per-site wind ratings (uniform Melbourne N2 baked into the
  catalogue for now)

---

## 5. Architecture

### 5.1 Data flow (unchanged)

```
pull_latest.py  →  <slug>/raw/*.pdf
extract.py      →  <slug>/extracted/{documents,plans}.json
quote.py        →  <slug>/quote/{quote.pdf, email.txt}      ← (was 4 files)
```

### 5.2 In-memory shape after `compute_quote()`

```python
{
    "priced":   [<line_item>, ...],   # status in {new, altered}
                                       # AND no review reasons
    "pending":  [<line_item>, ...],   # status in {new, altered}
                                       # AND has review reasons
    "subtotal": float,                # priced only
    "gst":      float,                # subtotal × 0.10
    "total":    float,                # subtotal × 1.10
    "n_priced": int,
    "n_pending": int,
}
```

`<line_item>` = the original `Window` dict + catalogue spec lookup +
sequential label + (`area_m2`, `total_aud`, `frame_weight_kg`) for
priced items, or (`review_reasons: list[str]`) for pending items.

### 5.3 Validation — review reasons

A renovation-scope item goes to the pending bucket if any of:

| Condition | Reason text rendered |
|---|---|
| `frame_width_mm` is null | "Width not labelled on drawings" |
| `frame_height_mm` is null | "Height not labelled on drawings" |
| Either dimension < 300 mm or > 3500 mm | "Dimension out of plausible range — bbox capture suspect" |
| Aspect ratio outside [0.28, 3.57] | "Aspect ratio implausible — bbox capture suspect" |
| `type == "unknown"` | "Window type not identifiable from drawing" |
| `(kind, type)` not in CATALOGUE | "Catalogue does not yet support this product type" |

Items with `status == null` are *excluded* from scope (not pending),
with a stderr warning so the human knows to check the extraction.

### 5.4 Item label scheme

After the renovation-scope filter, items are numbered in
walk-the-list order:

```
For each item in renovation_scope_order:
    if kind == "window": label = "W{:02d}".format(window_count); window_count += 1
    if kind == "door":   label = "D{:02d}".format(door_count);   door_count   += 1
```

No room name. No drawing-sheet name in the label (sheet still appears
inside the per-item block as a `Source: elevation 3` line).

### 5.5 Catalogue (12 entries, inline at top of `quote.py`)

```python
WIND_SPECS = {"deflection": 250, "sls_pa": 400, "uls_pa": 900, "pw_pa": 150}

CATALOGUE: dict[tuple[str, str], dict] = {
    ("window", "fixed"):       {framing, glass, hardware, finish,
                                 reveals, screen, rate_per_m2,
                                 kg_per_m2, u_value, shgc},
    ("window", "awning"):       {…},
    ("window", "casement"):     {…},
    ("window", "double_hung"):  {…},
    ("window", "sliding"):      {…},
    ("window", "louvre"):       {…},
    ("window", "highlight"):    {…},
    ("door",   "hinged"):       {…},
    ("door",   "sliding"):      {…},
    ("door",   "stacker"):      {…},
    ("door",   "french"):       {…},
    ("door",   "bifold"):       {…},
}
```

Full populated values are in §A1. Lookup misses → pending (see 5.3).

### 5.6 Quote header & sender block

Header (top of PDF page 1):

```
[ logo ]   Capital T Partners Pty Ltd                      QUOTATION
           ABN 56 664 499 825 · ACN 664 499 825          QN-9-ADMIRAL-V1
           2/7 English St, Essendon Fields VIC 3042       Date: {today}
           +61 418 127 492 · sava@capitaltpartners.com    Valid: {today+30}
                                                          Sales: {sender}
```

**Quote ID formula:** the first two slug segments, upper-cased, joined by `-`:

```python
def _quote_id(slug: str) -> str:
    return "QN-" + "-".join(slug.split("-")[:2]).upper() + "-V1"
```

Single-council demo has zero collision risk. Multi-council expansion may want a different scheme.

Customer block (just below):

```
Customer:    {applicant.name}
Property:    {project.address}
Project:     {project.title}
Drawings:    {practitioner.company} (job {project.job_number})
Phone:       {applicant.phone or "—"}
Email:       {applicant.email or "—"}
```

### 5.7 Per-item block layout (1–2 per page)

```
┌──────────────────────────────────────────────────────────────────┐
│  W{nn}  ·  {kind} ({type-display})  ·  {sheet}                   │
│                                                                  │
│  ┌────────────┐    Framing:    {catalogue.framing}               │
│  │            │    Dimension:  {w} × {h} mm    or  —             │
│  │     W      │    Finish:     {catalogue.finish}                │
│  │     ×      │    Glass:      {catalogue.glass}                 │
│  │     H      │    Hardware:   {catalogue.hardware}              │
│  │            │    Reveals:    {catalogue.reveals}               │
│  └────────────┘    Screen:     {catalogue.screen}                │
│   thumbnail        Frame Wt:   {kg} kg          or  —            │
│                    Wind:       Deflection 250 / SLS 400 /        │
│                                ULS 900 / Pw 150 Pa               │
│                    Energy:     Uw {u_value} · SHGCw {shgc}       │
│                                                                  │
│  [if pending]  ⚠  PENDING SITE MEASURE                           │
│                Reasons: {review_reasons joined by "; "}          │
└──────────────────────────────────────────────────────────────────┘
```

**Page format:** A4 portrait, Helvetica core font, 12 mm margin all sides, ~2 per-item blocks per page. Pagination automatic via `pdf.set_auto_page_break(auto=True, margin=12)`.

**Thumbnail rendering:** outer rectangle scaled into a fixed 30 × 30 mm box on the left of the block. Use `pdf.rect(x, y, 30, 30)` for the outline; W and H labels printed *outside* the rectangle (W under the bottom edge, H rotated 90° to the left edge) at `pdf.set_font("Helvetica", "", 6)`. For pending items, render the rectangle with a lighter draw colour (`pdf.set_draw_color(180, 180, 180)`) and no W/H labels; restore the default colour (`pdf.set_draw_color(0, 0, 0)`) afterwards.

**Field-rendering rule:** every catalogue field (`Framing`, `Finish`, `Glass`, `Hardware`, `Reveals`, `Screen`, `Wind`, `Energy`) prints on every priced item, even when the value is `"Not required"` / `"No screen"` — matches KB's layout where every slot prints. For pending items, `Dimension` and `Frame Wt` show `—`; catalogue fields still print with their normal values; the **PENDING SITE MEASURE** badge appears at the bottom of the block with the joined review reasons.

### 5.8 Totals block (after the last per-item block)

```
─────────────────────────────────────────────────────
   Priced items:    {n_priced} of {n_priced + n_pending}
   Pending items:   {n_pending}  (priced after site measure)

   Subtotal:                              ${subtotal:,.0f}
   GST (10%):                             ${gst:,.0f}
   Indicative Total:                      ${total:,.0f}
─────────────────────────────────────────────────────
```

### 5.9 Terms & Conditions (last page)

Verbatim from §A2.

---

## 6. Files touched

| File | Change |
|---|---|
| `scripts/quote.py` | Major rewrite (catalogue, validation, render) |
| `.env` | ✅ **DONE** — 8 `SAVA_SENDER_*` and `SAVA_LOGO_PATH` keys appended below the existing `GEMINI_API_KEY` (values per §A3) |
| `requirements` (in CLAUDE.md setup block) | Add `pillow>=10.0` and `fpdf2>=2.7` to the `pip install` line |
| `docs/pipeline.md` | Update three-stage description: §3 emits PDF + email only; mention pending bucket; mention catalogue |
| `lessons/lessons.md` | Add lesson: defaults removed in favour of pending bucket — invented dims previously masqueraded as measurements |

Files explicitly **not** touched:

- `scripts/extract.py` — already returns `null` for unknowns; that
  contract is what `quote.py` reads.
- `scripts/pull_latest.py` — out of scope.
- Any extracted JSON file — single source of truth; humans edit
  these directly during review iteration.

---

## 7. Implementation plan (ordered)

**Pre-completed (done in PRD-prep session):**

- ✅ `.env` populated at project root with the 9 Capital T Partners keys (see §A3).
- ✅ `capital-t-logo.webp` already present at project root.

**Remaining work:**

1. Add deps: `pip install "pillow>=10.0" "fpdf2>=2.7"`. Manually verify
   `fpdf.FPDF().image("capital-t-logo.webp", x=12, y=12, w=30)` succeeds
   on the demo machine.
2. Bake the `CATALOGUE` and `WIND_SPECS` dicts at top of `quote.py`.
   Add `Catalogue` lookup helper.
3. Add `_review_reasons(item, catalogue) -> list[str]` per §5.3.
   Delete `DEFAULT_SIZE_MM`, `PLAUSIBLE_MAX_MM`, `_resolve_size`,
   `_dim_str`, `_is_suspect`.
4. Add `_renovation_scope(plans) -> list[Window]` filter; warn on
   `status==null`.
5. Add `_assign_labels(items)` for `W01/D01` sequencing.
6. Rewrite `compute_quote()` to return the dual-bucket dict (§5.2).
7. Add `_quote_id(slug) -> str`, `_valid_until(today) -> date`.
8. Rewrite `render_pdf()`:
   - Header band (logo + entity + quote meta)
   - Customer block
   - Per-item blocks **in label order regardless of status** (D01, W01, W02, … walk-the-list); pending badges break up the flow naturally and reflect the audit narrative
   - Totals block
   - Terms page (last)
9. Rewrite `render_email()` to the audit-narrative template (§A4).
10. Drop `render_schedule()` and `render_quote()`. Update `main()`
    to write only `quote.pdf` and `email.txt`.
11. Run on `9-Admiral-Street-Seddon/`. Hand-check against §2.
12. Update `docs/pipeline.md` and add a `lessons/lessons.md` entry.

---

## Appendices

### A1. Catalogue values

⚠ **DRAFT — Sava to review before any real send.** The rates and energy values below are mid-point Australian-industry placeholders, not Sava-approved figures. The customer-facing PDF total is already labelled "Indicative" and bracketed by site-measure caveats, so the demo lands honestly — but a number quoted is a number implied. Lock real figures before any send.

```python
WIND_SPECS = {"deflection": 250, "sls_pa": 400, "uls_pa": 900, "pw_pa": 150}

CATALOGUE: dict[tuple[str, str], dict] = {
    # ── windows ────────────────────────────────────────────────────────
    ("window", "fixed"): {
        "framing":      "Commercial-grade aluminium fixed window system",
        "glass":        "Double-glazed Low-E 4/12/4 toughened",
        "hardware":     "Not required",
        "finish":       "Powder coat — Monument matt (or specified)",
        "reveals":      "138mm FJ primed pine",
        "screen":       "No screen",
        "rate_per_m2":  900,
        "kg_per_m2":    25,
        "u_value":      3.3,
        "shgc":         0.65,
    },
    ("window", "awning"): {
        "framing":      "Aluminium awning window system, single/multi-light",
        "glass":        "Double-glazed Low-E 4/12/4 toughened",
        "hardware":     "Std chain winder, lockable",
        "finish":       "Powder coat — Monument matt (or specified)",
        "reveals":      "138mm FJ primed pine",
        "screen":       "Fibreglass flyscreen mesh",
        "rate_per_m2":  1100,
        "kg_per_m2":    28,
        "u_value":      4.1,
        "shgc":         0.55,
    },
    ("window", "casement"): {
        "framing":      "Aluminium casement window system, side-hung",
        "glass":        "Double-glazed Low-E 4/12/4 toughened",
        "hardware":     "Std friction stay + key-locking handle",
        "finish":       "Powder coat — Monument matt (or specified)",
        "reveals":      "138mm FJ primed pine",
        "screen":       "Fibreglass flyscreen mesh",
        "rate_per_m2":  1150,
        "kg_per_m2":    28,
        "u_value":      4.0,
        "shgc":         0.56,
    },
    ("window", "double_hung"): {
        "framing":      "Aluminium double-hung window, vertical sliding sashes",
        "glass":        "Double-glazed Low-E 4/12/4 toughened",
        "hardware":     "Spring-balanced sashes, cam-action lock",
        "finish":       "Powder coat — Monument matt (or specified)",
        "reveals":      "138mm FJ primed pine",
        "screen":       "Fibreglass flyscreen mesh",
        "rate_per_m2":  1300,
        "kg_per_m2":    30,
        "u_value":      4.2,
        "shgc":         0.55,
    },
    ("window", "sliding"): {
        "framing":      "Aluminium horizontal sliding window system",
        "glass":        "Double-glazed Low-E 4/12/4 toughened",
        "hardware":     "Std cam lock, twin rollers",
        "finish":       "Powder coat — Monument matt (or specified)",
        "reveals":      "138mm FJ primed pine",
        "screen":       "Fibreglass flyscreen mesh",
        "rate_per_m2":  1000,
        "kg_per_m2":    27,
        "u_value":      4.3,
        "shgc":         0.55,
    },
    ("window", "louvre"): {
        "framing":      "Aluminium louvre window with adjustable glass blades",
        "glass":        "6mm clear toughened louvre blades",
        "hardware":     "Restricted-opening keyed handle (where required)",
        "finish":       "Powder coat — Monument matt (or specified)",
        "reveals":      "138mm FJ primed pine",
        "screen":       "Fibreglass flyscreen mesh",
        "rate_per_m2":  1500,
        "kg_per_m2":    26,
        "u_value":      5.6,
        "shgc":         0.62,
    },
    ("window", "highlight"): {
        "framing":      "Aluminium highlight (transom) fixed window",
        "glass":        "Double-glazed Low-E 4/12/4 toughened",
        "hardware":     "Not required",
        "finish":       "Powder coat — Monument matt (or specified)",
        "reveals":      "138mm FJ primed pine",
        "screen":       "No screen",
        "rate_per_m2":  950,
        "kg_per_m2":    25,
        "u_value":      3.4,
        "shgc":         0.65,
    },
    # ── doors ──────────────────────────────────────────────────────────
    ("door", "hinged"): {
        "framing":      "Aluminium hinged entry door, single panel",
        "glass":        "Double-glazed Low-E 4/12/4 toughened",
        "hardware":     "Lever set, 3-point lock, threshold seal",
        "finish":       "Powder coat — Monument matt (or specified)",
        "reveals":      "138mm FJ primed pine",
        "screen":       "No screen",
        "rate_per_m2":  2400,
        "kg_per_m2":    33,
        "u_value":      3.9,
        "shgc":         0.52,
    },
    ("door", "sliding"): {
        "framing":      "Aluminium 2-panel sliding door system",
        "glass":        "Double-glazed Low-E 4/12/4 toughened",
        "hardware":     "Cylinder + D-pull handle, stainless rollers",
        "finish":       "Powder coat — Monument matt (or specified)",
        "reveals":      "138mm FJ primed pine",
        "screen":       "Sub-frame flyscreen (optional)",
        "rate_per_m2":  1700,
        "kg_per_m2":    30,
        "u_value":      4.0,
        "shgc":         0.56,
    },
    ("door", "stacker"): {
        "framing":      "Aluminium 3-panel stacker door system",
        "glass":        "Double-glazed Low-E 4/12/4 toughened",
        "hardware":     "Double-cylinder lock, D-pull handle, stainless rollers",
        "finish":       "Powder coat — Monument matt (or specified)",
        "reveals":      "138mm FJ primed pine",
        "screen":       "Sub-frame flyscreen (optional)",
        "rate_per_m2":  2200,
        "kg_per_m2":    32,
        "u_value":      3.8,
        "shgc":         0.61,
    },
    ("door", "french"): {
        "framing":      "Aluminium French door pair, side-hung",
        "glass":        "Double-glazed Low-E 4/12/4 toughened",
        "hardware":     "Lever set + flush bolts, 3-point lock",
        "finish":       "Powder coat — Monument matt (or specified)",
        "reveals":      "138mm FJ primed pine",
        "screen":       "No screen",
        "rate_per_m2":  2000,
        "kg_per_m2":    31,
        "u_value":      4.0,
        "shgc":         0.55,
    },
    ("door", "bifold"): {
        "framing":      "Aluminium bifold door system, top-hung",
        "glass":        "Double-glazed Low-E 4/12/4 toughened",
        "hardware":     "Multi-point lock, magnetic catch, stainless tracks",
        "finish":       "Powder coat — Monument matt (or specified)",
        "reveals":      "138mm FJ primed pine",
        "screen":       "Optional retractable",
        "rate_per_m2":  2700,
        "kg_per_m2":    33,
        "u_value":      4.1,
        "shgc":         0.55,
    },
}
```

**Notes:**

- Rates are mid-point Melbourne residential 2025 ranges (installed: supply + fit + flashings + waste).
- `kg_per_m2` sanity-checked against KB QN5375: ~25 kg/m² fixed (item 1: 49 kg / 1.91 m²), ~28 kg/m² awning (item 7: 89 kg / 3.24 m²), ~25 kg/m² stacker (item 17: 181 kg / 7.2 m²).
- `u_value` and `shgc` track KB's spread: 3.3 fixed → 4.1 awning.
- Wind specs uniform across catalogue (Melbourne residential N2). Per-item override is out of scope.
- A `(kind, type)` lookup miss → item goes to the pending bucket with the catalogue-miss reason (§5.3 final row). Adding a new product type = one new dict entry, no renderer changes.

### A2. Terms & Conditions text

```
This is an indicative quotation based on advertised planning drawings.
Pricing is final after a 30-minute on-site measure. The terms below set
out the conditions of any subsequent firm quotation.

QUOTATION NOTES / TRADING TERMS

In these notes, "the Company" refers to: Capital T Partners Pty Ltd
                                          ABN 56 664 499 825
                                          ACN 664 499 825

We are pleased to submit this quotation for your consideration in
accordance with the comments above and our terms of sale. Please check
the schedule and confirm that the quantity, sizes and handing of joinery
are correct. (All units are shown from an external view point.)

It is your responsibility to ensure that the details in this quotation
are accurate before signing acceptance. Responsibility for accurate
ordering rests entirely with you.

All windows supplied need to be carefully installed and checked for
squareness. No liability is accepted for faulty installation or for
frames manufactured to sizes or details that subsequently change. All
variations will be charged. No plumbing or electrical work is included
unless specifically stated. Shop drawings are not supplied unless
specifically stated and incur an additional cost if requested.

ORDER CHECKLIST — please confirm before signing:
  1. Frame sizes correct (Frame Size vs. Stud Opening — these differ)
  2. Glass specification correct on all windows and doors
  3. Reveal linings correct on each opening (may vary by wall build-up)
  4. Hand of windows/doors correct (hinged: opens IN or OUT?)
  5. Diagrams reviewed from external view point
  6. Hardware colour confirmed (black is standard; specialty colours
     can add up to 5 weeks lead time)
  7. Recessed door sills allowed for in overall height (laundry combos
     especially)

Once this quotation is accepted, the Company is not responsible for any
incorrect sizes other than those explicitly stated. After acceptance and
materials ordering, any change incurs a fee for the additional cost of
labour and materials. A service fee applies if a Company representative
is required on site due to incorrect installation. A storage fee may
apply if windows are held in our facility beyond the agreed delivery
date.

PAYMENT TERMS
Payment terms will be confirmed at engagement, before any deposit is
collected. This indicative quotation does not constitute a payment
obligation.

ACCEPTANCE
I, _____________________________________________________ have read
and understood this quotation, reviewed the order checklist, and
hereby accept the quotation and the conditions above.

Print Name: _____________________________________________
Signature:  _____________________________________________
Date:       _____________________________________________
```

### A3. `.env` keys

✅ The `.env` file at project root has been populated with the values below. No action needed unless you want to override a specific field.

```env
# Capital T Partners Pty Ltd
SAVA_SENDER_NAME=Sava Trifunovic
SAVA_SENDER_COMPANY=Capital T Partners Pty Ltd
SAVA_SENDER_PHONE=+61 418 127 492
SAVA_SENDER_EMAIL=sava@capitaltpartners.com
SAVA_SENDER_WEB=capitaltpartners.com
SAVA_SENDER_ADDRESS=2/7 English St, Essendon Fields VIC 3042
SAVA_SENDER_ABN=56 664 499 825
SAVA_SENDER_ACN=664 499 825
SAVA_LOGO_PATH=capital-t-logo.webp

# Existing (unchanged)
GEMINI_API_KEY=…
```

### A4. Email body template

```
Subject: Window & glazed door scope — {short_address}

Hi {first_name},

I came across your planning application for {project_title} at
{address} (job {job_number} with {drawings_by}).

We're a Melbourne window installer — I worked through the renovation
scope from the drawings and put an indicative schedule together for
the {n_total} new and altered items.

Where I am:
  • {n_priced} item(s) confidently sized from the drawings
  • {n_pending} item(s) pending a 30-min site measure
  • Indicative subtotal (priced items only): ${subtotal:,.0f}
  • GST (10%): ${gst:,.0f}
  • Indicative total: ${total:,.0f}
  • Firm total locks at the site visit

Full per-item schedule + spec attached as a PDF. Each pending item
is listed with the reason it needs a measure (usually: dimensions
not labelled on the elevation).

A few notes:
  • Quote is valid 30 days.
  • All sizes subject to site measure before any manufacturer order.
  • Pricing is per our standard product spec — happy to swap in a
    different glass / finish / hardware once we talk.

Could you spare 30 minutes this week or next for a free on-site
measure? Happy to work around your schedule.

Cheers,
{sender_name}
{sender_company}
{sender_phone}
{sender_email}
{sender_web}
```

**Variable derivation:**

- `{first_name}`: `applicant.name.split()[0]` if applicant present; else `owners[0].name.split()[0]` if any owner; else `"there"`.
- `{short_address}`: `project.address.split(",")[0]` (street-line only).
- `{address}` and `{project_title}`: pass through `_humanize()` (existing helper preserves AU state codes when title-casing).
- `{drawings_by}`: `practitioner.company` if present; else `"the project draftsperson"`.
- `{job_number}`: `project.job_number` or `"?"` if null.
- `{n_total}`: `n_priced + n_pending`.

**Edge cases:**

- `n_priced == 0` (everything pending): replace the bulleted `Where I am:` block with the single line `All items pending site measure — firm total locks at the visit.`. Skip the subtotal/GST/total bullets and the "Indicative total locks" line.
- `n_pending == 0` (everything priced): drop the pending-count bullet and the sentence "Each pending item is listed with the reason it needs a measure (usually: dimensions not labelled on the elevation)." Keep everything else.
- Both `applicant` and `owners` absent: greeting is `"Hi there,"`, no recipient name in the body.

---

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Webp loading fails on the demo machine even with pillow installed | Convert to PNG once, swap `SAVA_LOGO_PATH=capital-t-logo.png`; PRD-compatible, no code change |
| 9 Admiral demo shows 5 of 6 items pending — looks empty | This is the intended audit-narrative tone. Email + PDF preamble lean into it. Verified against §2. |
| `(kind, type)` lookup miss on a future sample (e.g. council uses a type we don't have in the catalogue) | Pending bucket catches it gracefully, with a clear reason. Adding a catalogue entry is one PR. |
| Catalogue rates drift from market reality | Catalogue is inline; team edits one dict in `quote.py`. When a non-coder needs to tune prices, split to its own file (trigger documented in CLAUDE.md ethos). |
| Catalogue rates in §A1 are mid-point placeholders, not Sava-approved | DRAFT note pinned at the top of §A1; customer-facing total carries "Indicative" label and the indicative-quote preamble. Sava reviews the dict before any real send. |

---

## 9. Definition of done

1. All success criteria in §2 pass on the 9 Admiral sample.
2. `docs/pipeline.md` updated to describe the new behaviour.
3. `lessons/lessons.md` records the defaults-removed lesson.
4. Catalogue rates and energy values in §A1 reviewed and approved by
   Sava (or explicitly acknowledged as a pre-send blocker).
5. PRD reviewer confirms each row in §3 is implemented in `quote.py`.
