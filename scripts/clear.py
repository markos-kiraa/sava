"""Wipe the scraped/ directory. Used during scraper iteration to start
from a clean slate before re-running pull_latest.py.

Run:
    python scripts/clear.py        # prompts for confirmation
    python scripts/clear.py -y     # skip prompt
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRAPED = ROOT / "scraped"


def _summarize(path: Path) -> tuple[int, int]:
    files = 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            files += 1
            total += p.stat().st_size
    return files, total


def main(argv: list[str]) -> int:
    if not SCRAPED.exists():
        print(f"{SCRAPED.relative_to(ROOT)}/ does not exist — nothing to clear")
        return 0

    files, total = _summarize(SCRAPED)
    print(f"About to delete {SCRAPED.relative_to(ROOT)}/ "
          f"({files:,} files, {total:,} bytes)")

    if "-y" not in argv and "--yes" not in argv:
        if input("Proceed? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Aborted.")
            return 1

    shutil.rmtree(SCRAPED)
    print(f"Removed {SCRAPED.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
