"""Extract structured data from each application's PDFs into per-PDF JSON
files using Gemini 2.5 Pro. Idempotent: skips any output that already exists.

Outputs per app folder:
  <slug>/extracted/documents.json   from advertised-documents-*.pdf
  <slug>/extracted/plans.json       from advertised-plans-*.pdf
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
MODEL = "gemini-2.5-pro"

# ---------------------------------------------------------------------------
# Schemas
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


WindowStatus = Literal["existing", "new", "altered", "demolished"]


class Window(BaseModel):
    label: Optional[str] = None
    status: Optional[WindowStatus] = None
    approx_width_mm: Optional[int] = None
    approx_height_mm: Optional[int] = None
    sheet: Optional[str] = None
    notes: Optional[str] = None


class PlansExtraction(BaseModel):
    source_pdf: str
    project: Optional[Project] = None
    practitioner: Optional[Practitioner] = None
    windows: list[Window] = []


# ---------------------------------------------------------------------------
# Prompts
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

PLANS_PROMPT = """\
You are extracting structured data from a Victorian council architectural
plans PDF (Australia) for a residential project. The PDF contains drawings:
cover page, site plan, existing/proposed floor plans, elevations, sections,
and may include a window schedule.

Return JSON matching the provided schema. Rules:
- `project` comes from the sheet title block (title, project address, job
  number). The same title block usually repeats on every sheet.
- `practitioner` is the building designer/draftsperson/architect responsible
  for the drawings. Their stamp/contact block is on the cover page and/or
  bottom of every sheet. Capture name, company, phone, email, address, web,
  ABN. Leave any field null if not visible. Do NOT confuse the practitioner
  with the property owner or applicant.
- `windows` is a list of DISTINCT windows in the dwelling. Count each window
  exactly once, even though it appears on the floor plan AND multiple
  elevations. Use the floor plan as the authoritative source; elevations and
  any window schedule are for cross-checking dimensions. Include BOTH
  existing and proposed windows.
- For each window:
    * `label` — the schedule/drawing label like "W01" if present, else null.
    * `status` — one of:
        - "existing"    : present today and stays unchanged in the proposal
        - "new"         : a new window being installed in the extension
        - "altered"     : existing window being resized/replaced in place
        - "demolished"  : existing window being removed (often shown dashed
                          or annotated "to be removed" / "to be demolished")
      Use null only if you genuinely cannot determine the status.
    * `approx_width_mm` and `approx_height_mm` — dimensions in millimetres.
      Source these from the window schedule first, then from elevation
      annotations, then from drawing scale + measurement. If you genuinely
      cannot determine a dimension, leave it null. DO NOT invent numbers.
    * `sheet` — short reference like "Proposed Elevation A" or "Floor Plan".
    * `notes` — optional one-line context (e.g. "highlight window over
      kitchen", "double-hung", "obscure glazing") if it's clearly readable.
- DO NOT include doors. Windows only.
- Do not invent contact details. If the practitioner stamp is not visible,
  leave fields null.
"""


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------

def gemini_call(client: genai.Client, pdf_path: Path, prompt: str, schema_cls):
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
    return gemini_call(client, pdf_path, DOCS_PROMPT, DocumentsExtraction)


def extract_plans(client: genai.Client, pdf_path: Path) -> PlansExtraction:
    return gemini_call(client, pdf_path, PLANS_PROMPT, PlansExtraction)


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
