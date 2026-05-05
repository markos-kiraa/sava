"""Generate the outbound quote PDF + email body from extracted plans + documents.

Reads:
    <slug>/extracted/plans.json
    <slug>/extracted/documents.json

Writes:
    <slug>/quote/quote.pdf
    <slug>/quote/email.txt

Renovation scope is `windows` whose `status` is "new" or "altered". Items
with missing dimensions, implausible dimensions, unidentifiable types, or
no catalogue entry are routed to a pending bucket: rendered in the PDF
with a PENDING SITE MEASURE badge listing the reason(s), excluded from
the priced subtotal/GST/total. The deterministic-measurement principle
stands: the pipeline never invents dimensions to make a number print.

Sender details + logo path come from .env (SAVA_SENDER_*, SAVA_LOGO_PATH).

Run:
    python scripts/quote.py                # auto-detects first app folder
    python scripts/quote.py <slug>         # specify app folder
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

FONT_DIR = ROOT / "fonts"
FONT_REGULAR = FONT_DIR / "DejaVuSans.ttf"
FONT_BOLD = FONT_DIR / "DejaVuSans-Bold.ttf"
FONTS_AVAILABLE = FONT_REGULAR.exists() and FONT_BOLD.exists()

DASH = "—" if FONTS_AVAILABLE else "-"
WARN = "⚠" if FONTS_AVAILABLE else "[!]"
BULLET = "•" if FONTS_AVAILABLE else "-"
TIMES = "×" if FONTS_AVAILABLE else "x"

# Plausibility bounds. Width or height outside [DIM_MIN, DIM_MAX] is
# treated as a bbox-capture artefact (extractor swept in wall fragments).
DIM_MIN_MM = 300
DIM_MAX_MM = 3500
ASPECT_MIN = 0.28
ASPECT_MAX = 3.57

# Wind specs (Melbourne residential N2 baseline). Per-item override is
# out of scope for the single-council demo.
WIND_SPECS = {"deflection": 250, "sls_pa": 400, "uls_pa": 900, "pw_pa": 150}

# Per-(kind, type) product spec + commercial figures. Lookup miss → item
# routed to pending bucket with the catalogue-miss reason. Adding a new
# product type is a single dict entry; no renderer change needed.
#
# DRAFT — rates and energy values are mid-point Melbourne residential
# 2025 placeholders, not Sava-approved. Customer-facing total carries the
# "Indicative" label and the indicative-quotation preamble. Lock real
# figures before any real send.
CATALOGUE: dict[tuple[str, str], dict] = {
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

TERMS_TEXT = """\
This is an indicative quotation based on advertised planning drawings.
Pricing is final after a 30-minute on-site measure. The terms below set
out the conditions of any subsequent firm quotation.

QUOTATION NOTES / TRADING TERMS

In these notes, "the Company" refers to: {company}
                                          ABN {abn}
                                          ACN {acn}

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

ORDER CHECKLIST {dash} please confirm before signing:
  1. Frame sizes correct (Frame Size vs. Stud Opening {dash} these differ)
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
"""


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


_AU_STATE_CODES = {"VIC", "NSW", "QLD", "ACT", "WA", "SA", "NT", "TAS"}


def _humanize(text: str) -> str:
    """Title-case while preserving Australian state codes."""
    return " ".join(
        w if w.upper() in _AU_STATE_CODES else w.title()
        for w in text.split()
    )


def _au_date(d: date) -> str:
    """e.g. '4 May 2026'. AU customers expect day-first long form."""
    return d.strftime("%-d %B %Y")


def _format_phone(p: Optional[str]) -> str:
    """Pretty-print AU mobiles ('0437 160 077') and Sydney/Melbourne 8-digit
    landlines ('03 1234 5678'). Anything else passes through unchanged so
    we don't mangle international numbers."""
    if not p:
        return DASH
    digits = "".join(c for c in p if c.isdigit())
    if len(digits) == 10 and digits.startswith("04"):
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    if len(digits) == 10 and digits[0] == "0":
        return f"{digits[:2]} {digits[2:6]} {digits[6:]}"
    return p


# ---------------------------------------------------------------------------
# Validation + filtering
# ---------------------------------------------------------------------------


def _renovation_scope(plans: dict) -> list[dict]:
    """Items whose status indicates the customer is paying for them now:
    new builds and alterations. Existing/demolished are out of scope.
    `status==null` is excluded with a stderr warning so the human knows
    the extraction missed a status — pending bucket is for measurement
    gaps, not extraction gaps."""
    items = plans.get("windows") or []
    out = []
    for i, w in enumerate(items, 1):
        status = w.get("status")
        if status is None:
            print(f"  warn: item #{i} ({w.get('kind')} {w.get('type')}) "
                  f"has no status — excluded from scope", file=sys.stderr)
            continue
        if status in {"new", "altered"}:
            out.append(w)
    return out


def _review_reasons(item: dict) -> list[str]:
    reasons: list[str] = []
    w = item.get("frame_width_mm")
    h = item.get("frame_height_mm")
    if w is None:
        reasons.append("Width not labelled on drawings")
    if h is None:
        reasons.append("Height not labelled on drawings")
    if w is not None and h is not None:
        if w < DIM_MIN_MM or w > DIM_MAX_MM or h < DIM_MIN_MM or h > DIM_MAX_MM:
            reasons.append("Dimension out of plausible range — bbox capture suspect")
        else:
            short, long_ = sorted([w, h])
            ratio = short / long_
            if ratio < ASPECT_MIN or ratio > ASPECT_MAX:
                reasons.append("Aspect ratio implausible — bbox capture suspect")
    if item.get("type") == "unknown":
        reasons.append("Window type not identifiable from drawing")
    elif (item.get("kind"), item.get("type")) not in CATALOGUE:
        reasons.append("Catalogue does not yet support this product type")
    return reasons


# ---------------------------------------------------------------------------
# Quote computation
# ---------------------------------------------------------------------------


def _assign_labels(items: list[dict]) -> list[dict]:
    """Walk the renovation-scope list in order; assign W01/W02/... and
    D01/D02/... per kind. Order reflects how the items were encountered
    on the elevations — pending items keep their place in the flow."""
    window_n = 1
    door_n = 1
    out = []
    for item in items:
        labelled = dict(item)
        if item.get("kind") == "door":
            labelled["label_id"] = f"D{door_n:02d}"
            door_n += 1
        else:
            labelled["label_id"] = f"W{window_n:02d}"
            window_n += 1
        out.append(labelled)
    return out


def compute_quote(plans: dict) -> dict:
    scope = _assign_labels(_renovation_scope(plans))
    priced: list[dict] = []
    pending: list[dict] = []
    subtotal = 0.0
    for item in scope:
        reasons = _review_reasons(item)
        spec = CATALOGUE.get((item.get("kind"), item.get("type")))
        if reasons:
            pending.append({
                **item,
                "spec": spec,                       # may be None on catalogue miss
                "review_reasons": reasons,
            })
            continue
        # Priced path: dimensions present, plausible, type known, in catalogue.
        w = item["frame_width_mm"]
        h = item["frame_height_mm"]
        area = (w * h) / 1_000_000
        line_total = area * spec["rate_per_m2"]
        weight = area * spec["kg_per_m2"]
        priced.append({
            **item,
            "spec": spec,
            "area_m2": area,
            "total_aud": line_total,
            "frame_weight_kg": weight,
        })
        subtotal += line_total
    return {
        "scope_order": scope,                       # for in-order PDF rendering
        "priced": priced,
        "pending": pending,
        "subtotal": subtotal,
        "gst": subtotal * 0.10,
        "total": subtotal * 1.10,
        "n_priced": len(priced),
        "n_pending": len(pending),
    }


# ---------------------------------------------------------------------------
# Quote header helpers
# ---------------------------------------------------------------------------


def _quote_id(slug: str) -> str:
    return "QN-" + "-".join(slug.split("-")[:2]).upper() + "-V1"


def _valid_until(today: date) -> date:
    return today + timedelta(days=30)


def _customer_fields(documents: dict) -> dict:
    people = documents.get("people") or []
    applicant = next((p for p in people if p.get("role") == "applicant"), None)
    owners = [p for p in people if p.get("role") == "owner"]

    def cascade(field: str) -> Optional[str]:
        if applicant and applicant.get(field):
            return applicant[field]
        for o in owners:
            if o.get(field):
                return o[field]
        return None

    return {
        "name":  cascade("name") or DASH,
        "phone": _format_phone(cascade("phone")),
        "email": cascade("email") or DASH,
        "applicant": applicant,
        "owners": owners,
    }


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------


PAGE_MARGIN = 12
THUMB_CELL = 30  # mm — square cell that holds the aspect-preserved rect


def _font(pdf, style: str = "", size: int = 9) -> None:
    family = "DejaVu" if FONTS_AVAILABLE else "Helvetica"
    pdf.set_font(family, style, size)


def _setup_fonts(pdf) -> None:
    if FONTS_AVAILABLE:
        pdf.add_font("DejaVu", "", str(FONT_REGULAR))
        pdf.add_font("DejaVu", "B", str(FONT_BOLD))


def _type_display(t: Optional[str]) -> str:
    if not t or t == "unknown":
        return "Unknown"
    return t.replace("_", "-").title()


def _draw_header(pdf, sender: dict, quote_id: str, today: date,
                 valid_until: date) -> None:
    # Sava's logo is white wordmark + blue accent on transparent — designed
    # for dark backgrounds. We bake a navy "card" behind it so the wordmark
    # is legible without darkening the rest of the page.
    logo_w = 32
    logo_h = logo_w * 85 / 300        # source aspect 300 × 85
    logo_pad = 2.0
    card_w = logo_w + 2 * logo_pad
    card_h = logo_h + 2 * logo_pad
    logo_path = sender.get("logo_path")
    if logo_path and Path(logo_path).exists():
        try:
            pdf.set_fill_color(20, 40, 90)
            pdf.rect(PAGE_MARGIN, PAGE_MARGIN, card_w, card_h, style="F")
            pdf.image(logo_path, x=PAGE_MARGIN + logo_pad,
                      y=PAGE_MARGIN + logo_pad, w=logo_w)
        except Exception as e:
            print(f"  warn: logo render failed ({e}); continuing without",
                  file=sys.stderr)

    left_x = PAGE_MARGIN + card_w + 4
    pdf.set_xy(left_x, PAGE_MARGIN)
    _font(pdf, "B", 13)
    pdf.cell(110, 6, sender["company"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(left_x)
    _font(pdf, "", 8)
    pdf.cell(110, 4, f"ABN {sender['abn']} {BULLET} ACN {sender['acn']}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(left_x)
    pdf.cell(110, 4, sender["address"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(left_x)
    pdf.cell(110, 4, f"{sender['phone']} {BULLET} {sender['email']}",
             new_x="LMARGIN", new_y="NEXT")

    # Right-hand quote meta block
    right_x = 130
    pdf.set_xy(right_x, PAGE_MARGIN)
    _font(pdf, "B", 14)
    pdf.cell(70, 6, "QUOTATION", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(right_x)
    _font(pdf, "B", 10)
    pdf.cell(70, 5, quote_id, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(right_x)
    _font(pdf, "", 8)
    pdf.cell(70, 4, f"Date:  {_au_date(today)}", align="R",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(right_x)
    pdf.cell(70, 4, f"Valid until: {_au_date(valid_until)}", align="R",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(right_x)
    pdf.cell(70, 4, f"Sales: {sender['sender_name']}", align="R",
             new_x="LMARGIN", new_y="NEXT")

    pdf.set_y(PAGE_MARGIN + 28)
    pdf.set_draw_color(180, 180, 180)
    pdf.line(PAGE_MARGIN, pdf.get_y(), 210 - PAGE_MARGIN, pdf.get_y())
    pdf.set_draw_color(0, 0, 0)
    pdf.ln(2)


def _draw_customer_block(pdf, project: dict, practitioner: dict,
                         customer: dict) -> None:
    address = _humanize(project.get("address") or DASH)
    title = _humanize(project.get("title") or DASH)
    job_no = project.get("job_number") or DASH
    company = (_humanize(practitioner.get("company"))
               if practitioner.get("company") else DASH)

    rows = [
        ("Customer:",  customer["name"]),
        ("Property:",  address),
        ("Project:",   title),
        ("Drawings:",  f"{company} (job {job_no})"),
        ("Phone:",     customer["phone"]),
        ("Email:",     customer["email"]),
    ]
    for label, value in rows:
        _font(pdf, "B", 9)
        pdf.cell(22, 5, label)
        _font(pdf, "", 9)
        pdf.cell(0, 5, value, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_draw_color(180, 180, 180)
    pdf.line(PAGE_MARGIN, pdf.get_y(), 210 - PAGE_MARGIN, pdf.get_y())
    pdf.set_draw_color(0, 0, 0)
    pdf.ln(3)


def _draw_glyph(pdf, rx: float, ry: float, rw: float, rh: float,
                kind: Optional[str], type_: Optional[str]) -> None:
    """Schematic operation glyph inside the frame rect, derived from `type`
    only (Phase 1 — no panel layout from extraction). Conventions:
    chevron apex points to hinge side; "F" = fixed lite; horizontal arrow
    = slide direction. Hinge side defaults to left where the drawing
    doesn't tell us."""
    if not type_:
        return
    cx = rx + rw / 2
    cy = ry + rh / 2
    pad = 0.6
    pdf.set_line_width(0.15)
    pdf.set_draw_color(110, 110, 110)

    if type_ == "awning":
        pdf.line(rx + pad, ry + rh - pad, cx, ry + pad)
        pdf.line(rx + rw - pad, ry + rh - pad, cx, ry + pad)
    elif type_ == "casement":
        pdf.line(rx + rw - pad, ry + pad, rx + pad, cy)
        pdf.line(rx + rw - pad, ry + rh - pad, rx + pad, cy)
    elif type_ == "double_hung":
        pdf.line(rx + pad, cy, rx + rw - pad, cy)
    elif type_ == "louvre":
        n = 5
        for i in range(1, n + 1):
            yy = ry + rh * i / (n + 1)
            pdf.line(rx + pad * 2, yy, rx + rw - pad * 2, yy)
    elif type_ in ("sliding", "stacker"):
        pdf.line(cx, ry + pad, cx, ry + rh - pad)
        ax1 = cx + pad * 2
        ax2 = rx + rw - pad * 2
        if ax2 > ax1 + 1:
            pdf.line(ax1, cy, ax2, cy)
            head = min(1.2, (ax2 - ax1) / 3)
            pdf.line(ax2, cy, ax2 - head, cy - head / 2)
            pdf.line(ax2, cy, ax2 - head, cy + head / 2)
    elif type_ == "bifold":
        n = 4
        amp = min(rh * 0.18, 1.2)
        for i in range(n):
            x1 = rx + pad + (rw - 2 * pad) * i / n
            x2 = rx + pad + (rw - 2 * pad) * (i + 1) / n
            y1 = cy - amp if i % 2 == 0 else cy + amp
            y2 = cy + amp if i % 2 == 0 else cy - amp
            pdf.line(x1, y1, x2, y2)
    elif type_ == "french":
        pdf.line(cx, ry + pad, cx, ry + rh - pad)
        pdf.line(rx + pad, ry + pad, cx - pad, cy)
        pdf.line(rx + pad, ry + rh - pad, cx - pad, cy)
        pdf.line(rx + rw - pad, ry + pad, cx + pad, cy)
        pdf.line(rx + rw - pad, ry + rh - pad, cx + pad, cy)
    elif type_ == "hinged":
        pdf.line(rx + rw - pad, ry + pad, rx + pad, cy)
        pdf.line(rx + rw - pad, ry + rh - pad, rx + pad, cy)
    elif type_ == "fixed":
        pdf.set_text_color(110, 110, 110)
        _font(pdf, "B", 8)
        pdf.set_xy(rx, cy - 2)
        pdf.cell(rw, 4, "F", align="C")
        pdf.set_text_color(0, 0, 0)
    elif type_ == "highlight":
        pdf.set_text_color(110, 110, 110)
        _font(pdf, "B", 6)
        pdf.set_xy(rx, cy - 1.5)
        pdf.cell(rw, 3, "H", align="C")
        pdf.set_text_color(0, 0, 0)

    pdf.set_line_width(0.2)
    pdf.set_draw_color(0, 0, 0)


def _draw_thumbnail(pdf, x: float, y: float, w_mm: Optional[int],
                    h_mm: Optional[int], kind: Optional[str] = None,
                    type_: Optional[str] = None) -> None:
    """Aspect-preserving rectangle inside a fixed THUMB_CELL square, with
    a schematic operation glyph when type_ is supplied. For pending items
    (w_mm or h_mm is None) renders an empty light-grey outline at full
    cell size with no glyph and no W/H labels."""
    if w_mm is None or h_mm is None:
        pdf.set_draw_color(180, 180, 180)
        pdf.rect(x, y, THUMB_CELL, THUMB_CELL)
        pdf.set_draw_color(0, 0, 0)
        return

    if w_mm >= h_mm:
        rect_w = THUMB_CELL
        rect_h = THUMB_CELL * h_mm / w_mm
    else:
        rect_w = THUMB_CELL * w_mm / h_mm
        rect_h = THUMB_CELL
    rx = x + (THUMB_CELL - rect_w) / 2
    ry = y + (THUMB_CELL - rect_h) / 2
    pdf.set_draw_color(0, 0, 0)
    pdf.rect(rx, ry, rect_w, rect_h)
    _draw_glyph(pdf, rx, ry, rect_w, rect_h, kind, type_)

    _font(pdf, "", 6)
    # W label under the bottom edge
    pdf.set_xy(rx, ry + rect_h + 0.5)
    pdf.cell(rect_w, 2.5, f"W {w_mm}", align="C")
    # H label rotated 90° CCW, sitting cleanly to the LEFT of the rect.
    # Rotation maps the cell's width axis to "up" and its height axis to
    # "right". So a cell drawn at the pivot with width=rect_h, height=h_strip
    # ends up as a vertical strip from y=pivot.y-rect_h..pivot.y, occupying
    # x=pivot.x..pivot.x+h_strip. Pad to leave a gap from the rect edge.
    label_h = f"H {h_mm}"
    h_strip = 2.5
    pad = 1
    pivot_x = rx - pad - h_strip
    pivot_y = ry + rect_h
    with pdf.rotation(angle=90, x=pivot_x, y=pivot_y):
        pdf.set_xy(pivot_x, pivot_y)
        pdf.cell(rect_h, h_strip, label_h, align="C")


def _draw_item_block(pdf, item: dict, is_pending: bool) -> None:
    """A per-item card. Layout: 30mm thumbnail on the left, spec rows on
    the right, optional PENDING badge across the bottom.

    The block must render atomically — auto-pagination mid-block would
    strand the thumbnail on the previous page. So we estimate height
    upfront and force a page break if it won't fit."""
    # Header (~6mm) + 10 spec rows × 4.4mm + thumbnail-vs-spec slack +
    # bottom separator (~5mm). Pending adds badge (~5mm) + reasons line
    # (~5mm) + ln (~2mm).
    spec_h = 10 * 4.4
    body_h = max(spec_h, THUMB_CELL + 3)
    needed = 6 + body_h + 5
    if is_pending:
        needed += 12
    page_bottom = pdf.h - pdf.b_margin
    if pdf.get_y() + needed > page_bottom:
        pdf.add_page()

    block_top = pdf.get_y()
    text_x = PAGE_MARGIN + 5 + THUMB_CELL + 4   # thumb_x + cell width + gap
    text_w = 210 - text_x - PAGE_MARGIN

    # Header line: "W01 · door (Stacker) · elevation 3"
    sheet = item.get("sheet") or DASH
    type_disp = _type_display(item.get("type"))
    kind = item.get("kind") or "?"
    header = (f"{item['label_id']}  {BULLET}  {kind} ({type_disp})  "
              f"{BULLET}  {sheet}")
    _font(pdf, "B", 10)
    pdf.set_xy(PAGE_MARGIN, block_top)
    pdf.cell(0, 5, header, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    # Thumbnail (left) + spec rows (right). Indent by 5mm so the rotated H
    # label on priced thumbnails has clearance from the page margin.
    body_top = pdf.get_y()
    thumb_x = PAGE_MARGIN + 5
    thumb_y = body_top + 1
    w_mm = None if is_pending else item.get("frame_width_mm")
    h_mm = None if is_pending else item.get("frame_height_mm")
    _draw_thumbnail(pdf, thumb_x, thumb_y, w_mm, h_mm,
                    kind=item.get("kind"), type_=item.get("type"))

    spec = item.get("spec")  # may be None on catalogue miss
    if not is_pending:
        dim_str = f"{item['frame_width_mm']} {TIMES} {item['frame_height_mm']} mm"
        weight_str = f"{item['frame_weight_kg']:.1f} kg"
    else:
        dim_str = DASH
        weight_str = DASH

    def field(name: str, value: str) -> None:
        _font(pdf, "B", 8)
        pdf.set_x(text_x)
        pdf.cell(22, 4.4, name)
        _font(pdf, "", 8)
        pdf.cell(text_w - 22, 4.4, value, new_x="LMARGIN", new_y="NEXT")

    pdf.set_xy(text_x, body_top)
    if spec is None:
        # Catalogue miss — every catalogue field renders as DASH so the
        # block keeps its shape regardless of which check tripped.
        field("Framing:",   DASH)
        field("Dimension:", dim_str)
        field("Finish:",    DASH)
        field("Glass:",     DASH)
        field("Hardware:",  DASH)
        field("Reveals:",   DASH)
        field("Screen:",    DASH)
        field("Frame Wt:",  weight_str)
        field("Wind:",      DASH)
        field("Energy:",    DASH)
    else:
        field("Framing:",   spec["framing"])
        field("Dimension:", dim_str)
        field("Finish:",    spec["finish"])
        field("Glass:",     spec["glass"])
        field("Hardware:",  spec["hardware"])
        field("Reveals:",   spec["reveals"])
        field("Screen:",    spec["screen"])
        field("Frame Wt:",  weight_str)
        field("Wind:",      f"Deflection {WIND_SPECS['deflection']} / "
                            f"SLS {WIND_SPECS['sls_pa']} / "
                            f"ULS {WIND_SPECS['uls_pa']} / "
                            f"Pw {WIND_SPECS['pw_pa']} Pa")
        field("Energy:",    f"Uw {spec['u_value']} {BULLET} "
                            f"SHGCw {spec['shgc']}")

    body_end = pdf.get_y()
    bottom = max(body_end, thumb_y + THUMB_CELL + 3)
    pdf.set_y(bottom)

    if is_pending:
        pdf.ln(1)
        _font(pdf, "B", 9)
        pdf.set_text_color(160, 60, 0)
        pdf.cell(0, 5, f"{WARN}  PENDING SITE MEASURE",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        _font(pdf, "", 8)
        reasons = "; ".join(item["review_reasons"])
        pdf.set_x(PAGE_MARGIN)
        pdf.multi_cell(0, 4, f"Reasons: {reasons}",
                       new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    pdf.ln(2)
    pdf.set_draw_color(220, 220, 220)
    pdf.line(PAGE_MARGIN, pdf.get_y(), 210 - PAGE_MARGIN, pdf.get_y())
    pdf.set_draw_color(0, 0, 0)
    pdf.ln(3)


def _draw_totals(pdf, q: dict) -> None:
    pdf.ln(2)
    pdf.set_draw_color(0, 0, 0)
    pdf.line(PAGE_MARGIN, pdf.get_y(), 210 - PAGE_MARGIN, pdf.get_y())
    pdf.ln(2)

    n_total = q["n_priced"] + q["n_pending"]
    _font(pdf, "B", 10)
    pdf.cell(0, 6, f"Priced items: {q['n_priced']} of {n_total}",
             new_x="LMARGIN", new_y="NEXT")
    if q["n_pending"] > 0:
        _font(pdf, "", 9)
        pdf.cell(0, 5,
                 f"Pending items: {q['n_pending']}  (priced after site measure)",
                 new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    if q["n_priced"] == 0:
        _font(pdf, "B", 11)
        pdf.cell(0, 6,
                 "All items pending site measure {0} firm total locks at the visit.".format(DASH),
                 new_x="LMARGIN", new_y="NEXT")
    else:
        label_w = 130
        val_w = 210 - 2 * PAGE_MARGIN - label_w
        _font(pdf, "", 10)
        pdf.cell(label_w, 6, "Subtotal:", align="R")
        pdf.cell(val_w, 6, f"${q['subtotal']:,.0f}",
                 align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(label_w, 6, "GST (10%):", align="R")
        pdf.cell(val_w, 6, f"${q['gst']:,.0f}",
                 align="R", new_x="LMARGIN", new_y="NEXT")
        _font(pdf, "B", 11)
        pdf.cell(label_w, 8, "Indicative Total:", align="R")
        pdf.cell(val_w, 8, f"${q['total']:,.0f}",
                 align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(2)
    pdf.set_draw_color(0, 0, 0)
    pdf.line(PAGE_MARGIN, pdf.get_y(), 210 - PAGE_MARGIN, pdf.get_y())


def _draw_terms_page(pdf, sender: dict) -> None:
    pdf.add_page()
    _font(pdf, "B", 13)
    pdf.cell(0, 7, "Terms & Conditions", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    body = TERMS_TEXT.format(
        company=sender["company"],
        abn=sender["abn"],
        acn=sender["acn"],
        dash=DASH,
    )
    _font(pdf, "", 9)
    for line in body.split("\n"):
        pdf.multi_cell(0, 4.4, line, new_x="LMARGIN", new_y="NEXT")


def render_pdf(plans: dict, documents: dict, q: dict, slug: str,
               out_path: Path) -> None:
    from fpdf import FPDF

    project = plans.get("project") or {}
    practitioner = plans.get("practitioner") or {}
    customer = _customer_fields(documents)

    sender = {
        "company":     os.getenv("SAVA_SENDER_COMPANY", "Sava Windows"),
        "sender_name": os.getenv("SAVA_SENDER_NAME", "[Your Name]"),
        "phone":       os.getenv("SAVA_SENDER_PHONE", "[Your Phone]"),
        "email":       os.getenv("SAVA_SENDER_EMAIL", "[Your Email]"),
        "address":     os.getenv("SAVA_SENDER_ADDRESS", "[Your Address]"),
        "abn":         os.getenv("SAVA_SENDER_ABN", "[ABN]"),
        "acn":         os.getenv("SAVA_SENDER_ACN", "[ACN]"),
        "logo_path":   os.getenv("SAVA_LOGO_PATH"),
    }
    if sender["logo_path"]:
        p = Path(sender["logo_path"])
        sender["logo_path"] = str(p if p.is_absolute() else ROOT / p)

    today = date.today()
    quote_id = _quote_id(slug)
    valid_until = _valid_until(today)

    pdf = FPDF(orientation="P", format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=PAGE_MARGIN)
    pdf.set_margins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
    _setup_fonts(pdf)
    pdf.add_page()

    _draw_header(pdf, sender, quote_id, today, valid_until)
    _draw_customer_block(pdf, project, practitioner, customer)

    # Walk renovation scope in original order; mark each as priced/pending.
    pending_ids = {p["label_id"] for p in q["pending"]}
    for item in q["scope_order"]:
        is_pending = item["label_id"] in pending_ids
        # Pull the enriched dict (with spec/area/etc) back from the right bucket
        bucket = q["pending"] if is_pending else q["priced"]
        enriched = next(x for x in bucket if x["label_id"] == item["label_id"])
        _draw_item_block(pdf, enriched, is_pending)

    _draw_totals(pdf, q)
    _draw_terms_page(pdf, sender)

    pdf.output(str(out_path))


# ---------------------------------------------------------------------------
# Email rendering
# ---------------------------------------------------------------------------


def render_email(plans: dict, documents: dict, q: dict) -> str:
    project = plans.get("project") or {}
    practitioner = plans.get("practitioner") or {}
    customer = _customer_fields(documents)

    if customer["applicant"]:
        recipient_name = customer["applicant"]["name"]
    elif customer["owners"]:
        recipient_name = customer["owners"][0].get("name") or ""
    else:
        recipient_name = ""
    first_name = recipient_name.split()[0] if recipient_name else "there"
    if first_name and first_name != "there":
        first_name = first_name.title()

    raw_address = project.get("address") or "your property"
    address = _humanize(raw_address)
    short_address = address.split(",")[0]
    project_title = _humanize(project.get("title") or "an extension")
    job_number = project.get("job_number") or "?"
    drawings_by = (_humanize(practitioner.get("company"))
                   if practitioner.get("company")
                   else "the project draftsperson")
    n_total = q["n_priced"] + q["n_pending"]

    sender_name = os.getenv("SAVA_SENDER_NAME", "[Your Name]")
    sender_company = os.getenv("SAVA_SENDER_COMPANY", "Sava Windows")
    sender_phone = os.getenv("SAVA_SENDER_PHONE", "[Your Phone]")
    sender_email = os.getenv("SAVA_SENDER_EMAIL", "[Your Email]")
    sender_web = os.getenv("SAVA_SENDER_WEB", "")

    greeting = f"Hi {first_name}," if recipient_name else "Hi there,"
    subject = f"Window & glazed door scope {DASH} {short_address}"

    # "Where I am" block branches three ways.
    if q["n_priced"] == 0:
        where_block = (
            f"All items pending site measure {DASH} firm total locks at the visit."
        )
    elif q["n_pending"] == 0:
        where_block = (
            f"Where I am:\n"
            f"  {BULLET} {q['n_priced']} item(s) confidently sized from the drawings\n"
            f"  {BULLET} Indicative subtotal: ${q['subtotal']:,.0f}\n"
            f"  {BULLET} GST (10%): ${q['gst']:,.0f}\n"
            f"  {BULLET} Indicative total: ${q['total']:,.0f}\n"
            f"  {BULLET} Firm total locks at the site visit"
        )
    else:
        where_block = (
            f"Where I am:\n"
            f"  {BULLET} {q['n_priced']} item(s) confidently sized from the drawings\n"
            f"  {BULLET} {q['n_pending']} item(s) pending a 30-min site measure\n"
            f"  {BULLET} Indicative subtotal (priced items only): "
            f"${q['subtotal']:,.0f}\n"
            f"  {BULLET} GST (10%): ${q['gst']:,.0f}\n"
            f"  {BULLET} Indicative total: ${q['total']:,.0f}\n"
            f"  {BULLET} Firm total locks at the site visit"
        )

    pending_sentence = (
        " Each pending item is listed with the reason it needs a measure "
        "(usually: dimensions not labelled on the elevation)."
        if q["n_pending"] > 0 else ""
    )

    body_lines = [
        f"Subject: {subject}",
        "",
        greeting,
        "",
        f"I came across your planning application for {project_title} at",
        f"{address} (job {job_number} with {drawings_by}).",
        "",
        f"We're a Melbourne window installer {DASH} I worked through the renovation",
        f"scope from the drawings and put an indicative schedule together for",
        f"the {n_total} new and altered items.",
        "",
        where_block,
        "",
        (f"Full per-item schedule + spec attached as a PDF."
         f"{pending_sentence}"),
        "",
        "A few notes:",
        f"  {BULLET} Quote is valid 30 days.",
        f"  {BULLET} All sizes subject to site measure before any manufacturer order.",
        f"  {BULLET} Pricing is per our standard product spec {DASH} happy to swap in a",
        f"    different glass / finish / hardware once we talk.",
        "",
        "Could you spare 30 minutes this week or next for a free on-site",
        "measure? Happy to work around your schedule.",
        "",
        "Cheers,",
        sender_name,
        sender_company,
        sender_phone,
        sender_email,
    ]
    if sender_web:
        body_lines.append(sender_web)
    return "\n".join(body_lines) + "\n"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _resolve_app_dir(argv: list[str]) -> Path | None:
    if len(argv) > 1:
        candidate = Path(argv[1])
        if candidate.is_absolute() or candidate.exists():
            return candidate
        return ROOT / argv[1]
    candidates = sorted(
        p for p in ROOT.iterdir()
        if p.is_dir() and (p / "extracted" / "plans.json").exists()
    )
    return candidates[0] if candidates else None


def main(argv: list[str]) -> int:
    app_dir = _resolve_app_dir(argv)
    if app_dir is None or not (app_dir / "extracted" / "plans.json").exists():
        print("No app folder with extracted/plans.json found.", file=sys.stderr)
        return 1
    app_dir = app_dir.resolve()

    plans = _load_json(app_dir / "extracted" / "plans.json")
    docs_path = app_dir / "extracted" / "documents.json"
    documents = _load_json(docs_path) if docs_path.exists() else {}

    q = compute_quote(plans)
    email_txt = render_email(plans, documents, q)

    out_dir = app_dir / "quote"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "email.txt").write_text(email_txt)
    render_pdf(plans, documents, q, app_dir.name, out_dir / "quote.pdf")

    print(f"Priced: {q['n_priced']}  Pending: {q['n_pending']}  "
          f"Subtotal: ${q['subtotal']:,.0f}  Total: ${q['total']:,.0f}")
    for name in ("quote.pdf", "email.txt"):
        print(f"Wrote: {out_dir.relative_to(ROOT)}/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
