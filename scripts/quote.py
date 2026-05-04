"""Generate a window schedule + draft quote + outbound email body from
extracted plans + documents.

Reads:
    <slug>/extracted/plans.json
    <slug>/extracted/documents.json

Writes (and also prints schedule + quote to stdout):
    <slug>/quote/schedule.md
    <slug>/quote/quote.txt
    <slug>/quote/quote.pdf
    <slug>/quote/email.txt

Pricing:
    Per m² installed rates (supply + fit + flashings + waste). Demo
    placeholders — to be replaced with the team's real price book.

Item handling:
    - frame dims missing  -> uses sensible default size, marked "*est"
    - frame dims implausible (>plausible_max range) -> uses default,
      marked "*VERIFY" so installer eyeballs the drawing before sending
    - frame dims valid -> priced from measurement, no flag

Sender details for the outbound email come from .env (SAVA_SENDER_NAME,
SAVA_SENDER_PHONE, SAVA_SENDER_EMAIL) or fall back to bracket placeholders.

Run:
    python scripts/quote.py                # auto-detects first app folder
    python scripts/quote.py <slug>         # specify app folder
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# AUD per m², installed (material + labour + flashings + waste). Demo
# placeholders. Real price book replaces these.
RATE_PER_M2_AUD = {
    "window": 1200,
    "door":   1600,
}

# Defaults for items where the drawing yielded no measurable dimensions.
DEFAULT_SIZE_MM = {
    "window": (1200, 1500),
    "door":   (2400, 2100),
}

# A measurement outside this range is treated as suspect (likely the
# extractor captured part of a wall or ceiling fragment).
PLAUSIBLE_MAX_MM = {
    "window": (3500, 2400),
    "door":   (5000, 2700),
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _is_suspect(item: dict) -> bool:
    w, h = item.get("frame_width_mm"), item.get("frame_height_mm")
    if w is None or h is None:
        return False
    max_w, max_h = PLAUSIBLE_MAX_MM.get(item["kind"], (4000, 3000))
    return w > max_w or h > max_h


def _resolve_size(item: dict) -> tuple[int, int, str]:
    w, h = item.get("frame_width_mm"), item.get("frame_height_mm")
    if w is None or h is None:
        dw, dh = DEFAULT_SIZE_MM.get(item["kind"], (1200, 1500))
        return dw, dh, "default"
    if _is_suspect(item):
        dw, dh = DEFAULT_SIZE_MM.get(item["kind"], (1200, 1500))
        return dw, dh, "suspect"
    return w, h, "measured"


def _dim_str(item: dict) -> str:
    w, h, source = _resolve_size(item)
    flag = {"measured": "", "default": " *est", "suspect": " *VERIFY"}[source]
    return f"{w} × {h}{flag}"


def compute_quote(plans: dict) -> dict:
    """Build the priced line-item list + totals once. Format functions
    consume this rather than recomputing."""
    line_items = []
    subtotal = 0.0
    for w in plans["windows"]:
        wt, ht, source = _resolve_size(w)
        area = (wt * ht) / 1_000_000
        rate = RATE_PER_M2_AUD.get(w["kind"], 1200)
        total = area * rate
        line_items.append({
            **w,
            "width_mm_resolved": wt,
            "height_mm_resolved": ht,
            "source": source,
            "area_m2": area,
            "rate_aud_per_m2": rate,
            "total_aud": total,
        })
        subtotal += total
    return {
        "line_items": line_items,
        "subtotal": subtotal,
        "gst": subtotal * 0.10,
        "total": subtotal * 1.10,
    }


def render_schedule(plans: dict) -> str:
    project = plans.get("project") or {}
    practitioner = plans.get("practitioner") or {}
    out = []
    out.append("# Window Schedule")
    out.append("")
    out.append(f"**Project:** {project.get('title') or '?'}")
    out.append(f"**Address:** {project.get('address') or '?'}")
    out.append(f"**Drawings:** {practitioner.get('company') or '?'}"
               f" (job {project.get('job_number') or '?'})")
    out.append("")
    out.append("| #  | Sheet       | Kind   | Type        | Status     | W × H (mm)            |")
    out.append("|----|-------------|--------|-------------|------------|-----------------------|")
    for i, w in enumerate(plans["windows"], 1):
        sheet = (w.get("sheet") or "?")[:11]
        wtype = w["type"].replace("_", "-")
        status = (w.get("status") or "?")
        out.append(
            f"| {i:<2} | {sheet:<11} | {w['kind']:<6} | {wtype:<11} | "
            f"{status:<10} | {_dim_str(w):<21} |"
        )
    out.append("")
    return "\n".join(out)


def render_quote(plans: dict, documents: dict, q: dict) -> str:
    project = plans.get("project") or {}
    practitioner = plans.get("practitioner") or {}
    people = documents.get("people", []) or []
    applicant = next((p for p in people if p.get("role") == "applicant"), None)
    owners = [p for p in people if p.get("role") == "owner"]

    out = []
    out.append("=" * 78)
    out.append(" DRAFT QUOTE — windows & glazed doors")
    out.append("=" * 78)
    if applicant:
        out.append(f" Attn:        {applicant.get('name', '?')}")
    elif owners:
        out.append(f" Attn:        {owners[0].get('name', 'Property Owner')}")
    out.append(f" Property:    {project.get('address') or '?'}")
    out.append(f" Project:     {project.get('title') or '?'}")
    drawings_by = practitioner.get("company") or "?"
    job_no = project.get("job_number") or "?"
    out.append(f" Drawings:    {drawings_by} (job {job_no})")
    out.append(f" Date:        {date.today().isoformat()}")
    out.append("")
    out.append(f" {'#':<3} {'Description':<46} {'m²':>6} {'$/m²':>7} {'AUD':>10}")
    out.append(" " + "-" * 76)
    for i, item in enumerate(q["line_items"], 1):
        suffix = {"measured": "", "default": " *est",
                  "suspect": " *VERIFY"}[item["source"]]
        type_str = item["type"].replace("_", "-")
        sheet = item.get("sheet") or "?"
        status = item.get("status") or "?"
        desc = f"[{status}] {item['kind']} ({type_str}) — {sheet}{suffix}"
        out.append(
            f" {i:<3} {desc:<46.46} {item['area_m2']:>6.2f} "
            f"{item['rate_aud_per_m2']:>7,} {item['total_aud']:>10,.0f}"
        )
    out.append(" " + "-" * 76)
    out.append(f" {'Subtotal':>67} {q['subtotal']:>10,.0f}")
    out.append(f" {'GST (10%)':>67} {q['gst']:>10,.0f}")
    out.append(f" {'TOTAL':>67} {q['total']:>10,.0f}")
    out.append("")
    out.append(" Notes:")
    out.append(" • All sizes subject to site measure before manufacturer order.")
    out.append(" • Rates are installed inclusive (supply, fit, flashings, waste).")
    out.append(" • Items marked '*est' use typical defaults pending site measure.")
    out.append(" • Items marked '*VERIFY' have implausible measurements — inspect drawing.")
    out.append(" • Status reflects the architect's drawings; existing items may not require")
    out.append("   replacement — confirm scope with the customer before ordering.")
    out.append(" • Quote valid 30 days. Excludes structural alterations.")
    out.append("=" * 78)
    return "\n".join(out)


_AU_STATE_CODES = {"VIC", "NSW", "QLD", "ACT", "WA", "SA", "NT", "TAS"}


def _humanize(text: str) -> str:
    """Title-case while preserving Australian state codes."""
    return " ".join(
        w if w.upper() in _AU_STATE_CODES else w.title()
        for w in text.split()
    )


def render_email(plans: dict, documents: dict, q: dict) -> str:
    project = plans.get("project") or {}
    practitioner = plans.get("practitioner") or {}
    people = documents.get("people", []) or []
    applicant = next((p for p in people if p.get("role") == "applicant"), None)
    owners = [p for p in people if p.get("role") == "owner"]

    recipient = (
        applicant.get("name") if applicant
        else owners[0].get("name") if owners
        else "there"
    )
    first_name = recipient.split()[0] if recipient and recipient != "there" else "there"

    raw_address = project.get("address") or "your property"
    address = _humanize(raw_address)
    short_address = address.split(",")[0]
    raw_title = project.get("title") or "an extension"
    project_title = _humanize(raw_title)
    job_no = project.get("job_number") or "?"
    drawings_by = (practitioner.get("company") or "the project draftsperson").title()
    n_items = len(q["line_items"])

    sender_name = os.getenv("SAVA_SENDER_NAME", "[Your Name]")
    sender_phone = os.getenv("SAVA_SENDER_PHONE", "[Your Phone]")
    sender_email = os.getenv("SAVA_SENDER_EMAIL", "[Your Email]")
    sender_company = os.getenv("SAVA_SENDER_COMPANY", "Sava Windows")

    subject = f"Window & glazed door quote — {short_address}"

    body = f"""Subject: {subject}

Hi {first_name},

I came across your planning application for {project_title.lower()}
at {address} (job {job_no} with {drawings_by}).

We're a Melbourne window installer — I went through the drawings and put
together an indicative quote based on the {n_items} windows and glazed doors
visible across the existing and proposed elevations:

  Line items: {n_items}
  Subtotal:   ${q['subtotal']:,.0f}
  GST (10%):  ${q['gst']:,.0f}
  TOTAL:      ${q['total']:,.0f}

The full schedule + line-by-line breakdown is attached.

A few things worth flagging:
  • All sizes subject to site measure before any manufacturer order.
  • Some items use default dimensions where the drawings didn't print
    them — final numbers lock at the site visit.
  • Quote is valid for 30 days.

Happy to come out and do a free site measure — would you have 30 minutes
this week or next?

Cheers,
{sender_name}
{sender_company}
{sender_phone}
{sender_email}
"""
    return body.strip() + "\n"


def render_pdf(plans: dict, documents: dict, q: dict, out_path: Path) -> None:
    """Render a 1-page A4 quote PDF at out_path. ASCII-only — fpdf2's core
    fonts are Latin-1 and the unicode chars in the .txt version (×, •, —)
    don't render cleanly there."""
    from fpdf import FPDF

    project = plans.get("project") or {}
    practitioner = plans.get("practitioner") or {}
    people = documents.get("people", []) or []
    applicant = next((p for p in people if p.get("role") == "applicant"), None)
    owners = [p for p in people if p.get("role") == "owner"]

    sender_company = os.getenv("SAVA_SENDER_COMPANY", "Sava Windows")
    sender_name = os.getenv("SAVA_SENDER_NAME", "[Your Name]")
    sender_phone = os.getenv("SAVA_SENDER_PHONE", "[Your Phone]")
    sender_email = os.getenv("SAVA_SENDER_EMAIL", "[Your Email]")

    pdf = FPDF(orientation="P", format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    # Header band
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, sender_company, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "Draft Quote - Windows & Glazed Doors",
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Date: {date.today().isoformat()}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Customer / project block
    raw_address = project.get("address") or "?"
    address = _humanize(raw_address)
    project_title = _humanize(project.get("title") or "?")
    drawings_by = (practitioner.get("company") or "?").title()
    job_no = project.get("job_number") or "?"

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 5, "Property", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    if applicant:
        pdf.cell(0, 5, f"Attn: {applicant['name']}",
                 new_x="LMARGIN", new_y="NEXT")
    elif owners:
        pdf.cell(0, 5, f"Attn: {owners[0].get('name', 'Property Owner')}",
                 new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, address, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Project: {project_title}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Drawings: {drawings_by} (job {job_no})",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Line-item table
    col_w = [8, 95, 18, 22, 28]   # # / desc / m2 / $/m2 / AUD
    headers = ["#", "Description", "m2", "$/m2", "AUD"]
    aligns = ["C", "L", "R", "R", "R"]

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(230, 230, 230)
    for w, htext, a in zip(col_w, headers, aligns):
        pdf.cell(w, 6, htext, border=1, fill=True, align=a)
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    for i, item in enumerate(q["line_items"], 1):
        suffix = {"measured": "", "default": " (est)",
                  "suspect": " (VERIFY)"}[item["source"]]
        type_str = item["type"].replace("_", "-")
        sheet = item.get("sheet") or "?"
        status = item.get("status") or "?"
        desc = f"[{status}] {item['kind']} ({type_str}) - {sheet}{suffix}"
        if len(desc) > 70:
            desc = desc[:67] + "..."
        cells = [
            str(i),
            desc,
            f"{item['area_m2']:.2f}",
            f"${item['rate_aud_per_m2']:,}",
            f"${item['total_aud']:,.0f}",
        ]
        for w, txt, a in zip(col_w, cells, aligns):
            pdf.cell(w, 5.5, txt, border=1, align=a)
        pdf.ln()

    # Totals
    pdf.ln(2)
    label_w = sum(col_w[:-1])
    val_w = col_w[-1]
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(label_w, 6, "Subtotal", align="R")
    pdf.cell(val_w, 6, f"${q['subtotal']:,.0f}",
             align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(label_w, 6, "GST (10%)", align="R")
    pdf.cell(val_w, 6, f"${q['gst']:,.0f}",
             align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(label_w, 8, "TOTAL", align="R")
    pdf.cell(val_w, 8, f"${q['total']:,.0f}",
             align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # Notes
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 5, "Notes", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    notes = [
        "All sizes subject to site measure before manufacturer order.",
        "Rates are installed inclusive (supply, fit, flashings, waste).",
        "Items marked '(est)' use typical defaults pending site measure.",
        "Items marked '(VERIFY)' have implausible measurements - inspect drawing.",
        "Status reflects the architect's drawings; existing items may not require "
        "replacement - confirm scope with the customer before ordering.",
        "Quote valid 30 days. Excludes structural alterations.",
    ]
    for n in notes:
        pdf.multi_cell(0, 4, f"- {n}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Sender signature
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, sender_name, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, sender_company, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, sender_phone, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, sender_email, new_x="LMARGIN", new_y="NEXT")

    pdf.output(str(out_path))


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
        print(f"No app folder with extracted/plans.json found.", file=sys.stderr)
        return 1

    plans = _load_json(app_dir / "extracted" / "plans.json")
    docs_path = app_dir / "extracted" / "documents.json"
    documents = _load_json(docs_path) if docs_path.exists() else {}

    q = compute_quote(plans)
    schedule_md = render_schedule(plans)
    quote_txt = render_quote(plans, documents, q)
    email_txt = render_email(plans, documents, q)

    out_dir = app_dir / "quote"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "schedule.md").write_text(schedule_md + "\n")
    (out_dir / "quote.txt").write_text(quote_txt + "\n")
    (out_dir / "email.txt").write_text(email_txt)
    render_pdf(plans, documents, q, out_dir / "quote.pdf")

    print(schedule_md)
    print(quote_txt)
    print()
    for name in ("schedule.md", "quote.txt", "quote.pdf", "email.txt"):
        print(f"Wrote: {out_dir.relative_to(ROOT)}/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
