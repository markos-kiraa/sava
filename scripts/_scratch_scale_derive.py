"""Step A: derive mm/pt scale per page by matching dimension labels (e.g.
"3,130") to the dimension lines they annotate, then computing
mm_per_pt = label_value_mm / line_length_pt.

Cross-checks across multiple labels on the same page — they must agree
within a tight tolerance.

Run:
    python scripts/_scratch_scale_derive.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple

import fitz  # pymupdf

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "9-Admiral-Street-Seddon" / "raw" / "advertised-plans-tp5120261-9-admiral-street-seddon.pdf"
PAGES_1IDX = [6, 7, 8]


# A dimension label like "3,130" or "1230" — 3-5 digits, optional thousands comma.
# Tighten to avoid catching job numbers (1216), sheet numbers (small), etc.
DIM_LABEL_RE = re.compile(r"^\s*(\d{1,2},?\d{3})\s*$")


class Span(NamedTuple):
    text: str
    value_mm: int
    bbox: tuple[float, float, float, float]
    orientation: str   # "h" or "v"
    centre: tuple[float, float]


class LineSeg(NamedTuple):
    p1: tuple[float, float]
    p2: tuple[float, float]
    length: float
    orientation: str   # "h", "v", or "d" (diagonal)


def parse_dim(text: str) -> int | None:
    m = DIM_LABEL_RE.match(text)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def text_spans(page: fitz.Page) -> list[Span]:
    spans: list[Span] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = span.get("text", "").strip()
                v = parse_dim(t)
                if v is None:
                    continue
                bb = span["bbox"]
                w = bb[2] - bb[0]
                h = bb[3] - bb[1]
                # Heuristic: chars are ~0.5–0.7 × font size wide.
                # If bbox is taller than wide, the text is rotated 90°.
                orient = "v" if h > w else "h"
                spans.append(Span(
                    text=t,
                    value_mm=v,
                    bbox=tuple(round(x, 2) for x in bb),
                    orientation=orient,
                    centre=((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2),
                ))
    return spans


def line_segments(page: fitz.Page) -> list[LineSeg]:
    segs: list[LineSeg] = []
    for path in page.get_drawings():
        last = None
        for item in path.get("items", []):
            op = item[0]
            if op == "l":
                p1, p2 = item[1], item[2]
                p1t = (round(p1.x, 2), round(p1.y, 2))
                p2t = (round(p2.x, 2), round(p2.y, 2))
                dx = abs(p2t[0] - p1t[0])
                dy = abs(p2t[1] - p1t[1])
                length = (dx * dx + dy * dy) ** 0.5
                if dx < 0.5:
                    orient = "v"
                elif dy < 0.5:
                    orient = "h"
                else:
                    orient = "d"
                segs.append(LineSeg(p1t, p2t, length, orient))
                last = p2t
            elif op == "m":
                last = (round(item[1].x, 2), round(item[1].y, 2))
    return segs


def nearest_dim_line(label: Span, lines: list[LineSeg]) -> LineSeg | None:
    """Find the line segment that this dimension label most likely annotates.

    Heuristic:
      - Line orientation must match the label's orientation
        (vertical-rotated label → vertical line; horizontal label → horizontal line).
      - The label's centre should sit near the line's perpendicular axis,
        within a small offset (label is placed beside the line it labels).
      - Among candidates, pick the longest (dimension lines are the long ones,
        not the short tick marks at endpoints).
    """
    cx, cy = label.centre
    candidates: list[tuple[float, LineSeg]] = []

    for seg in lines:
        if seg.orientation != label.orientation:
            continue
        if seg.length < 5:           # ignore tiny ticks
            continue
        if seg.length > 1000:        # ignore page-spanning rules
            continue

        if seg.orientation == "v":
            line_x = (seg.p1[0] + seg.p2[0]) / 2
            line_y_min = min(seg.p1[1], seg.p2[1])
            line_y_max = max(seg.p1[1], seg.p2[1])
            perp_dist = abs(line_x - cx)
            along_inside = line_y_min - 5 <= cy <= line_y_max + 5
        else:
            line_y = (seg.p1[1] + seg.p2[1]) / 2
            line_x_min = min(seg.p1[0], seg.p2[0])
            line_x_max = max(seg.p1[0], seg.p2[0])
            perp_dist = abs(line_y - cy)
            along_inside = line_x_min - 5 <= cx <= line_x_max + 5

        if perp_dist > 30:           # too far from the line
            continue
        if not along_inside:         # label is past the line's ends
            continue
        candidates.append((perp_dist, seg))

    if not candidates:
        return None
    # Among lines close enough perpendicularly, pick the longest.
    candidates.sort(key=lambda c: (c[0] > 15, -c[1].length))
    return candidates[0][1]


def main() -> int:
    doc = fitz.open(PDF)
    print(f"PDF: {PDF.name}")

    for p1 in PAGES_1IDX:
        page = doc[p1 - 1]
        print(f"\n=== page {p1} ===")
        spans = text_spans(page)
        lines = line_segments(page)
        print(f"  dim labels found:    {len(spans)}")
        print(f"  line segments total: {len(lines)}")

        results: list[tuple[Span, LineSeg, float]] = []
        for s in spans:
            ln = nearest_dim_line(s, lines)
            if ln is None:
                continue
            mm_per_pt = s.value_mm / ln.length
            results.append((s, ln, mm_per_pt))

        print(f"  matched (label, line) pairs: {len(results)}/{len(spans)}")
        for s, ln, mmpt in results[:40]:
            print(f"    {s.value_mm:>5} mm  | line {ln.orientation} len={ln.length:6.2f}pt"
                  f"  -> {mmpt:6.3f} mm/pt"
                  f"  | label_centre=({s.centre[0]:.1f},{s.centre[1]:.1f})")

        if results:
            mmpts = sorted(r[2] for r in results)
            n = len(mmpts)
            median = mmpts[n // 2] if n % 2 else (mmpts[n // 2 - 1] + mmpts[n // 2]) / 2
            print(f"  scale candidates (mm/pt): min={min(mmpts):.3f}  median={median:.3f}  max={max(mmpts):.3f}")
            # Expected at 1:100 -> 1pt × 25.4/72 mm/pt × 100 ≈ 35.28 mm/pt.
            # Expected at 1:200 -> ≈ 70.56 mm/pt.
            print(f"  expected:  1:100 → 35.28 mm/pt   1:200 → 70.56 mm/pt")

    doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
