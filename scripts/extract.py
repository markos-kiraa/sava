"""Extract structured data from each application's PDFs into per-PDF JSON
files. Idempotent: skips any output that already exists.

For documents.pdf: a single Gemini 2.5 Pro call against the whole PDF.
For plans.pdf: a hybrid pipeline. Gemini identifies each window/door on
each elevation page; we measure their dimensions deterministically from
the PDF's vector geometry using printed scale references on the page.

Outputs per app folder:
  <slug>/extracted/documents.json   from advertised-documents-*.pdf
  <slug>/extracted/plans.json       from advertised-plans-*.pdf
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Literal, Optional

import fitz  # pymupdf
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
MODEL = "gemini-2.5-pro"

# Pages (1-indexed) for the current Maribyrnong sample. Pages 4 + 5 are
# existing elevations, pages 8 + 9 are proposed elevations. Pages 1 + 7
# are used for project + practitioner title-block info. This is hardcoded
# for the single-council demo; will generalise when we add more councils.
ELEVATION_PAGE_NUMBERS = [4, 5, 8, 9]
METADATA_PAGE_NUMBERS = [1, 7]


# ---------------------------------------------------------------------------
# documents.json schemas
# ---------------------------------------------------------------------------

Role = Literal[
    "owner", "surveyor", "mortgagee", "builder", "architect",
    "draftsperson", "applicant", "other",
]


class TitleRef(BaseModel):
    volume: Optional[str] = None
    folio: Optional[str] = None
    lot: Optional[str] = None


class Property(BaseModel):
    address: Optional[str] = None
    title: Optional[TitleRef] = None


class Person(BaseModel):
    role: Role
    name: str
    company: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None


class ContactsFound(BaseModel):
    builder: Optional[str] = None
    architect: Optional[str] = None
    draftsperson: Optional[str] = None


class DocumentsExtraction(BaseModel):
    source_pdf: str
    application_id: Optional[str] = None
    property: Optional[Property] = None
    people: list[Person] = []
    contacts_found: ContactsFound = ContactsFound()


# ---------------------------------------------------------------------------
# plans.json schemas
# ---------------------------------------------------------------------------

WindowStatus = Literal["existing", "new", "altered", "demolished"]
WindowKind = Literal["window", "door"]
WindowType = Literal[
    # windows
    "fixed", "awning", "casement", "double_hung", "sliding",
    "louvre", "highlight",
    # glazed doors
    "stacker", "bifold", "french", "hinged",
    # fallback when the drawing doesn't show enough detail
    "unknown",
]


class Project(BaseModel):
    title: Optional[str] = None
    address: Optional[str] = None
    job_number: Optional[str] = None


class Practitioner(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    web: Optional[str] = None
    abn: Optional[str] = None


class Window(BaseModel):
    label: Optional[str] = None
    kind: WindowKind = "window"
    type: WindowType = "unknown"
    status: Optional[WindowStatus] = None
    # Frame outer dimensions as drawn on the elevation. Subject to site
    # measure for rough-opening conversion.
    frame_width_mm: Optional[int] = None
    frame_height_mm: Optional[int] = None
    sheet: Optional[str] = None
    notes: Optional[str] = None


class PlansExtraction(BaseModel):
    source_pdf: str
    project: Optional[Project] = None
    practitioner: Optional[Practitioner] = None
    windows: list[Window] = []


# ---------------------------------------------------------------------------
# documents.pdf — whole-PDF prompt + Gemini call
# ---------------------------------------------------------------------------

DOCS_PROMPT = """\
You are extracting structured data from a Victorian council "advertised
planning documents" PDF (Australia). The PDF may include a title search,
surveyor plans, written-statement compliance checklists, and a scanned
application form. Some sections are image-only — read them visually.

Return JSON matching the provided schema. Rules:
- Only fill a field if the PDF explicitly contains it. Use null otherwise.
- `application_id` looks like "TP51/2026(1)" or similar.
- `people` should include EVERY identifiable person/company with a role:
  property owners, mortgagees, surveyors, architects, draftspersons,
  builders, applicants. Use the closed-set role enum exactly as given;
  use "other" only if none fit.
- `company` is the firm/org if distinct from `name`.
- `contacts_found` summarises whether builder/architect/draftsperson contact
  info (name + phone or email) was identified anywhere in this PDF — value
  is the person's display name if found, else null.
- Do not invent emails/phones; if not in the document, leave null.
"""


def gemini_call_pdf(client: genai.Client, pdf_path: Path, prompt: str, schema_cls):
    pdf_part = types.Part.from_bytes(
        data=pdf_path.read_bytes(),
        mime_type="application/pdf",
    )
    resp = client.models.generate_content(
        model=MODEL,
        contents=[pdf_part, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema_cls,
        ),
    )
    data = json.loads(resp.text)
    data["source_pdf"] = pdf_path.name
    return schema_cls.model_validate(data)


def extract_documents(client: genai.Client, pdf_path: Path) -> DocumentsExtraction:
    return gemini_call_pdf(client, pdf_path, DOCS_PROMPT, DocumentsExtraction)


# ---------------------------------------------------------------------------
# plans.pdf — vector measurement helpers
# ---------------------------------------------------------------------------

# A dimension label like "3,130" or "1230" — 3-5 digit mm values, optional
# thousands comma. Tighter than \d+ to avoid catching job numbers (1216),
# small sheet numbers, etc.
_DIM_LABEL_RE = re.compile(r"^\s*(\d{1,2},?\d{3})\s*$")


def _dim_label_value(text: str) -> int | None:
    m = _DIM_LABEL_RE.match(text)
    return int(m.group(1).replace(",", "")) if m else None


def _line_segments(page: fitz.Page) -> list[tuple]:
    """All line segments on the page as (p1, p2, length, orientation) tuples,
    where orientation is "h" (horizontal), "v" (vertical), or "d" (diagonal).
    """
    segs: list[tuple] = []
    for path in page.get_drawings():
        for item in path.get("items", []):
            if item[0] != "l":
                continue
            p1, p2 = item[1], item[2]
            x1, y1, x2, y2 = p1.x, p1.y, p2.x, p2.y
            dx, dy = abs(x2 - x1), abs(y2 - y1)
            if dx < 0.5:
                orient = "v"
            elif dy < 0.5:
                orient = "h"
            else:
                orient = "d"
            length = (dx * dx + dy * dy) ** 0.5
            segs.append(((x1, y1), (x2, y2), length, orient))
    return segs


def _text_dim_spans(page: fitz.Page) -> list[tuple]:
    """Dimension-like text spans on the page: (value_mm, bbox, orient, centre)."""
    out = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = span.get("text", "").strip()
                v = _dim_label_value(t)
                if v is None:
                    continue
                bb = span["bbox"]
                w, h = bb[2] - bb[0], bb[3] - bb[1]
                # Vertical bbox (taller than wide) means the text is rotated 90°
                # and labels a vertical dimension line.
                orient = "v" if h > w else "h"
                centre = ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
                out.append((v, bb, orient, centre))
    return out


def derive_scale_mm_per_pt(page: fitz.Page) -> float | None:
    """Derive the page's mm-per-point scale by matching dimension labels to
    nearby dimension lines.

    Each (label, line) pair gives a candidate mm/pt. Multiple labels on the
    same page must converge to the same scale (e.g. 35.28 for 1:100).
    Returns the mean of the densest cluster, or None if fewer than 2
    independent labels agree within 1%.
    """
    spans = _text_dim_spans(page)
    lines = _line_segments(page)

    candidates: list[float] = []
    for value_mm, _bb, orient, centre in spans:
        cx, cy = centre
        best = None
        best_perp = float("inf")
        for (p1, p2, length, line_orient) in lines:
            if line_orient != orient or length < 5 or length > 1000:
                continue
            if line_orient == "v":
                line_x = (p1[0] + p2[0]) / 2
                ymin, ymax = sorted([p1[1], p2[1]])
                if not (ymin - 5 <= cy <= ymax + 5):
                    continue
                perp = abs(line_x - cx)
            else:
                line_y = (p1[1] + p2[1]) / 2
                xmin, xmax = sorted([p1[0], p2[0]])
                if not (xmin - 5 <= cx <= xmax + 5):
                    continue
                perp = abs(line_y - cy)
            if perp > 30:
                continue
            # Among nearby lines prefer the longest (real dim line, not a tick)
            if best is None or (perp < 15 and (best_perp >= 15 or length > best[2])) \
               or (best_perp >= 15 and perp < best_perp):
                best = (p1, p2, length, line_orient)
                best_perp = perp
        if best is not None:
            candidates.append(value_mm / best[2])

    if len(candidates) < 2:
        return None

    # Find the densest cluster (multiple labels agreeing within 1%).
    candidates.sort()
    best_cluster: list[float] = []
    for i, c in enumerate(candidates):
        cluster = [d for d in candidates[i:] if d <= c * 1.01]
        if len(cluster) > len(best_cluster):
            best_cluster = cluster
    if len(best_cluster) < 2:
        return None
    return sum(best_cluster) / len(best_cluster)


def measure_rect_in_region(
    page: fitz.Page,
    region_pt: tuple[float, float, float, float],
    scale_mm_per_pt: float,
) -> tuple[int, int] | None:
    """For a region in PDF point coordinates, find the bounding rect of all
    horizontal+vertical line segments whose midpoints fall inside a slightly
    padded version of the region, and convert to mm.

    The padding (5% of bbox dim, min 3pt) compensates for tightness errors
    in Gemini's bbox: legitimate frame-edge lines often have endpoints
    fractionally outside. The midpoint-inside test recovers those without
    pulling in long wall lines whose midpoints sit elsewhere on the page.
    """
    x1, y1, x2, y2 = region_pt
    if x2 <= x1 or y2 <= y1:
        return None

    bbox_w = x2 - x1
    bbox_h = y2 - y1
    pad_x = max(3.0, bbox_w * 0.05)
    pad_y = max(3.0, bbox_h * 0.05)
    sx1, sy1, sx2, sy2 = x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y
    # Frame outline lines are at most as long as the bbox's longer side
    # (with slack for tightness errors). Anything much longer is a wall,
    # eave, or roofline whose midpoint happens to fall inside the bbox.
    max_line_length = max(bbox_w, bbox_h) * 1.2

    h_x_extents: list[tuple[float, float]] = []
    v_y_extents: list[tuple[float, float]] = []

    for (p1, p2, length, orient) in _line_segments(page):
        if orient == "d" or length > max_line_length:
            continue
        mx = (p1[0] + p2[0]) / 2
        my = (p1[1] + p2[1]) / 2
        if not (sx1 <= mx <= sx2 and sy1 <= my <= sy2):
            continue
        if orient == "h":
            h_x_extents.append((min(p1[0], p2[0]), max(p1[0], p2[0])))
        else:
            v_y_extents.append((min(p1[1], p2[1]), max(p1[1], p2[1])))

    if not h_x_extents or not v_y_extents:
        return None

    width_pt = max(e[1] for e in h_x_extents) - min(e[0] for e in h_x_extents)
    height_pt = max(e[1] for e in v_y_extents) - min(e[0] for e in v_y_extents)

    if width_pt < 10 or height_pt < 10:
        return None

    width_mm = round(width_pt * scale_mm_per_pt)
    height_mm = round(height_pt * scale_mm_per_pt)

    if not (200 <= width_mm <= 4000 and 200 <= height_mm <= 4000):
        return None

    return (width_mm, height_mm)


def render_page_jpeg(page: fitz.Page, dpi: int = 200) -> bytes:
    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    return page.get_pixmap(matrix=matrix).tobytes("jpeg")


def bbox_norm_to_pt(
    bbox_norm: list[int],
    page_rect: fitz.Rect,
) -> tuple[float, float, float, float] | None:
    """Convert Gemini's normalised [ymin, xmin, ymax, xmax] (0-1000) to a
    PDF-point rect (x1, y1, x2, y2). Returns None if malformed.
    """
    if len(bbox_norm) != 4:
        return None
    ymin, xmin, ymax, xmax = bbox_norm
    if not (0 <= xmin < xmax <= 1000 and 0 <= ymin < ymax <= 1000):
        return None
    return (
        xmin / 1000.0 * page_rect.width,
        ymin / 1000.0 * page_rect.height,
        xmax / 1000.0 * page_rect.width,
        ymax / 1000.0 * page_rect.height,
    )


# ---------------------------------------------------------------------------
# plans.pdf — Gemini extraction (per-elevation + metadata)
# ---------------------------------------------------------------------------

class ElevationItem(BaseModel):
    kind: WindowKind = "window"
    type: WindowType = "unknown"
    status: Optional[WindowStatus] = None
    notes: str = ""
    sheet: Optional[str] = None
    bbox_norm: list[int] = Field(default_factory=list)


class ElevationExtraction(BaseModel):
    items: list[ElevationItem] = []


ELEVATION_PROMPT = """\
You are looking at a single sheet from an Australian residential planning
permit drawing set. The sheet contains one or more architectural elevation
drawings (front, side, or rear views of the house) at 1:100 scale.

For every WINDOW and GLAZED DOOR on this sheet, return one item with:
- kind: "window" or "door".
- type: ONE OF
    {fixed, awning, casement, double_hung, sliding, louvre, highlight} for windows
    {sliding, stacker, bifold, french, hinged} for glazed doors
  Use "unknown" if you genuinely cannot tell from the drawing — do not guess.
- status: one of {existing, new, altered, demolished}.
    * Sheets titled "existing" → all elements there are "existing".
    * Sheets titled "proposed" → elements visible in the new extension or
      annotated as new are "new"; elements unchanged from the existing house
      are "existing"; demolished elements are usually shown dashed or
      annotated "to be removed".
  Use null only if you genuinely cannot determine the status.
- notes: short description naming the room/wall it serves where you can
  tell from elevation context (e.g. "front facade — Bed 3 window", "rear
  sliding door to deck", "highlight window over kitchen").
- sheet: the elevation label printed near the drawing (e.g. "elevation 1",
  "elevation A", "existing elevation D"). Use the printed casing and
  numbering exactly. If the same sheet has multiple labelled elevations
  (e.g. "elevation 3" + "elevation 4"), assign each item to the elevation
  it sits within.
- bbox_norm: a 4-element array [ymin, xmin, ymax, xmax] giving the
  window/door's bounding box on the page in normalized 0-1000 coordinates.
  The box should TIGHTLY enclose the visible frame outline — do NOT
  include surrounding wall, eaves, sill detail, or trim. Tighter is
  better; loose bboxes cause measurement errors downstream.

EXCLUDE:
- Solid (non-glazed) doors.
- Decorative shutters, eaves, gutters, rooflines, downpipes, fascia.
- Title-block elements, scale bars, north points, dimension lines.
"""


def gemini_extract_elevation(
    client: genai.Client,
    image_bytes: bytes,
) -> ElevationExtraction:
    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    resp = client.models.generate_content(
        model=MODEL,
        contents=[image_part, ELEVATION_PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ElevationExtraction,
        ),
    )
    return ElevationExtraction.model_validate_json(resp.text)


class MetadataExtraction(BaseModel):
    project: Optional[Project] = None
    practitioner: Optional[Practitioner] = None


METADATA_PROMPT = """\
You are looking at one or two sheets from an Australian residential
planning-permit drawing set. The title block (usually at the bottom of
each sheet) contains the project info and the practitioner's stamp.

Return:
- project: title, full property address, and job number from the title block.
- practitioner: the building designer / draftsperson / architect responsible
  for the drawings. Capture name, company, phone, email, postal address,
  web, ABN — leaving any field null if it isn't visible. Do NOT confuse
  the practitioner with the property owner or applicant.
"""


def gemini_extract_metadata(
    client: genai.Client,
    image_bytes_list: list[bytes],
) -> MetadataExtraction:
    parts: list = [
        types.Part.from_bytes(data=b, mime_type="image/jpeg")
        for b in image_bytes_list
    ]
    resp = client.models.generate_content(
        model=MODEL,
        contents=[*parts, METADATA_PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=MetadataExtraction,
        ),
    )
    return MetadataExtraction.model_validate_json(resp.text)


# ---------------------------------------------------------------------------
# plans.pdf — orchestration
# ---------------------------------------------------------------------------

def extract_plans(client: genai.Client, pdf_path: Path) -> PlansExtraction:
    doc = fitz.open(pdf_path)

    # Metadata (project + practitioner) from cover + a drawing sheet.
    meta_imgs = [
        render_page_jpeg(doc[n - 1]) for n in METADATA_PAGE_NUMBERS
        if n <= len(doc)
    ]
    metadata = gemini_extract_metadata(client, meta_imgs)

    # Per-elevation: identify with Gemini, measure with vector geometry.
    windows: list[Window] = []
    for page_num in ELEVATION_PAGE_NUMBERS:
        if page_num > len(doc):
            print(f"  warn: page {page_num} not in PDF; skipping", file=sys.stderr)
            continue
        page = doc[page_num - 1]
        scale = derive_scale_mm_per_pt(page)
        if scale is None:
            print(f"  warn: page {page_num} no scale derivable; skipping", file=sys.stderr)
            continue

        try:
            elevation = gemini_extract_elevation(client, render_page_jpeg(page))
        except Exception as e:
            print(f"  error: gemini failed on page {page_num}: {e}", file=sys.stderr)
            continue

        for item in elevation.items:
            region_pt = bbox_norm_to_pt(item.bbox_norm, page.rect)
            dims = (
                measure_rect_in_region(page, region_pt, scale)
                if region_pt is not None else None
            )
            windows.append(Window(
                label=None,
                kind=item.kind,
                type=item.type,
                status=item.status,
                frame_width_mm=dims[0] if dims else None,
                frame_height_mm=dims[1] if dims else None,
                sheet=item.sheet,
                notes=item.notes,
            ))

    doc.close()
    return PlansExtraction(
        source_pdf=pdf_path.name,
        project=metadata.project,
        practitioner=metadata.practitioner,
        windows=windows,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

# (pdf_glob, output_filename, extractor_fn)
TASKS = [
    ("advertised-documents-*.pdf", "documents.json", extract_documents),
    ("advertised-plans-*.pdf",     "plans.json",     extract_plans),
]


def find_app_folders(root: Path) -> list[Path]:
    return sorted(
        p for p in root.iterdir()
        if p.is_dir() and not p.name.startswith(".") and (p / "raw").is_dir()
    )


def run_task(client: genai.Client, app_dir: Path, pdf_glob: str,
             output_name: str, extractor_fn) -> str:
    out_path = app_dir / "extracted" / output_name
    if out_path.exists():
        return f"skip   {app_dir.name}/extracted/{output_name}"

    pdf = next(iter((app_dir / "raw").glob(pdf_glob)), None)
    if pdf is None:
        return f"warn   {app_dir.name} (no {pdf_glob})"

    try:
        result = extractor_fn(client, pdf)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result.model_dump_json(indent=2))
        return f"wrote  {app_dir.name}/extracted/{output_name}"
    except Exception as e:
        return f"error  {app_dir.name}/{output_name}: {e}"


def main() -> int:
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set in .env", file=sys.stderr)
        return 1

    client = genai.Client(api_key=api_key)
    folders = find_app_folders(ROOT)
    if not folders:
        print(f"No app folders found in {ROOT}")
        return 0

    print(f"Scanning {len(folders)} app folder(s) in {ROOT}\n")
    for app_dir in folders:
        for glob, name, fn in TASKS:
            print(run_task(client, app_dir, glob, name, fn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
