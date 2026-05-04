"""Reconnaissance: dump what's actually in pages 6/7/8 of the Admiral St
PDF as vector primitives + text. We use this to design the real extractor.

Throwaway — leading underscore in filename signals that.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import fitz  # pymupdf

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "9-Admiral-Street-Seddon" / "raw" / "advertised-plans-tp5120261-9-admiral-street-seddon.pdf"
PAGES_1IDX = [6, 7, 8]


def main() -> int:
    doc = fitz.open(PDF)
    print(f"Pages in doc:    {len(doc)}")
    print(f"Encrypted:       {doc.is_encrypted}")
    print(f"Needs password:  {doc.needs_pass}")
    print(f"Permissions:     {doc.permissions}")

    if doc.needs_pass:
        ok = doc.authenticate("")
        print(f"  empty-password auth: {ok}")

    for p1 in PAGES_1IDX:
        page = doc[p1 - 1]
        print(f"\n=== page {p1} (idx {page.number}) ===")
        print(f"  rect (pts):   {page.rect}")
        print(f"  width × height (mm): "
              f"{page.rect.width / 72 * 25.4:.1f} × "
              f"{page.rect.height / 72 * 25.4:.1f}")

        try:
            drawings = page.get_drawings()
        except Exception as e:
            print(f"  get_drawings() failed: {e}")
            continue
        print(f"  draw paths:   {len(drawings)}")

        item_types: Counter = Counter()
        rect_count = 0
        line_count = 0
        rect_samples = []
        for d in drawings:
            kinds_in_path = set()
            for item in d.get("items", []):
                op = item[0]
                item_types[op] += 1
                kinds_in_path.add(op)
                if op == "re":
                    rect_count += 1
                    if len(rect_samples) < 5:
                        rect_samples.append(item[1])
                if op == "l":
                    line_count += 1
        print(f"  primitive ops: {dict(item_types)}")
        print(f"  total rects:   {rect_count}")
        print(f"  total lines:   {line_count}")
        print(f"  rect samples (first 5):")
        for r in rect_samples:
            print(f"    {r}  width={r.width:.1f}pt  height={r.height:.1f}pt")

        text = page.get_text()
        contains = {
            "3,130": "3,130" in text,
            "3130":  "3130"  in text,
            "1:100": "1:100" in text,
            "scale": "scale" in text.lower(),
            "elevation": "elevation" in text.lower(),
        }
        print(f"  text length:  {len(text)}")
        print(f"  contains:     {contains}")

        td = page.get_text("dict")
        spans_with_dim = []
        for block in td.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    s = span.get("text", "").strip()
                    if not s:
                        continue
                    if any(tok in s for tok in ("3,130", "3130", "1:100", "scale")):
                        spans_with_dim.append((span["bbox"], s, span.get("size")))
        print(f"  matching spans: {len(spans_with_dim)}")
        for bbox, s, size in spans_with_dim[:20]:
            print(f"    bbox={tuple(round(x, 1) for x in bbox)}  size={size}  text={s!r}")

    doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
