"""Find the most recently uploaded advertised-planning PDF on
maribyrnong.vic.gov.au and download it into a folder named after the application.
"""
from __future__ import annotations

import re
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin

from curl_cffi import requests
from bs4 import BeautifulSoup

BASE = "https://www.maribyrnong.vic.gov.au"
LISTING = f"{BASE}/Building-and-Planning/Advertised-Planning-Applications"
ROOT = Path(__file__).resolve().parent.parent

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-AU,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

session = requests.Session(impersonate="chrome131")


def polite_get(url: str, *, referer: str | None = None):
    h = {"Sec-Fetch-Site": "same-origin" if referer else "none"}
    if referer:
        h["Referer"] = referer
    r = session.get(url, headers=h, timeout=30)
    r.raise_for_status()
    time.sleep(0.4)
    return r


def list_application_urls() -> list[str]:
    soup = BeautifulSoup(polite_get(LISTING).content, "lxml")
    list_application_urls.referer = LISTING  # type: ignore[attr-defined]
    urls = []
    for a in soup.select("a[href*='/Advertised-Planning-Applications/']"):
        href = urljoin(BASE, a["href"])
        if href.rstrip("/").endswith("/Advertised-Planning-Applications"):
            continue
        if href not in urls:
            urls.append(href)
    return urls


def pdf_links_on(detail_url: str) -> list[str]:
    soup = BeautifulSoup(polite_get(detail_url, referer=LISTING).content, "lxml")
    pdfs = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            full = urljoin(BASE, href)
            if full not in pdfs:
                pdfs.append(full)
    return pdfs


def head_last_modified(url: str, referer: str):
    r = session.head(
        url,
        headers={"Sec-Fetch-Site": "same-origin", "Referer": referer},
        allow_redirects=True,
        timeout=30,
    )
    r.raise_for_status()
    time.sleep(0.2)
    lm = r.headers.get("Last-Modified")
    return parsedate_to_datetime(lm) if lm else None


def slug_from(detail_url: str) -> str:
    return detail_url.rstrip("/").rsplit("/", 1)[-1]


def safe_filename(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def main() -> int:
    print(f"Fetching listing: {LISTING}")
    apps = list_application_urls()
    print(f"  found {len(apps)} application detail pages")

    # Per-app: list of (last_modified, pdf_url). Apps with no datestamps are kept too.
    per_app: dict[str, list[tuple]] = {}
    for i, app in enumerate(apps, 1):
        print(f"[{i}/{len(apps)}] {app}")
        try:
            pdfs = pdf_links_on(app)
            per_app[app] = []
            for pdf in pdfs:
                lm = head_last_modified(pdf, referer=app)
                print(f"    {lm}  {pdf}")
                per_app[app].append((lm, pdf))
        except Exception as e:
            print(f"    ! error: {e}", file=sys.stderr)

    # Pick the app whose newest PDF is most recent.
    def app_max_lm(entries):
        stamps = [lm for lm, _ in entries if lm is not None]
        return max(stamps) if stamps else None

    ranked = [(app_max_lm(v), app) for app, v in per_app.items() if app_max_lm(v)]
    if not ranked:
        print("No PDFs with Last-Modified found.", file=sys.stderr)
        return 1
    ranked.sort(reverse=True)
    newest_lm, detail_url = ranked[0]

    app_dir = ROOT / slug_from(detail_url)
    raw_dir = app_dir / "raw"
    extracted_dir = app_dir / "extracted"
    raw_dir.mkdir(parents=True, exist_ok=True)
    extracted_dir.mkdir(parents=True, exist_ok=True)

    print()
    print(f"Latest application: {detail_url}")
    print(f"Newest PDF mtime:   {newest_lm.isoformat()}")
    print(f"App folder:         {app_dir}")
    print()

    pdfs_for_app = per_app[detail_url]
    print(f"Downloading {len(pdfs_for_app)} PDF(s) into raw/:")
    total_bytes = 0
    for lm, pdf_url in pdfs_for_app:
        dest = raw_dir / safe_filename(pdf_url)
        r = session.get(
            pdf_url,
            headers={"Sec-Fetch-Site": "same-origin", "Referer": detail_url},
            timeout=180,
        )
        r.raise_for_status()
        dest.write_bytes(r.content)
        size = dest.stat().st_size
        total_bytes += size
        print(f"  {size:>10,} B  raw/{dest.name}")
        time.sleep(0.4)

    print(f"\nDone. {total_bytes:,} bytes total in {app_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
