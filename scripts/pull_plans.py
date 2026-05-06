"""Download every advertised-planning PDF on maribyrnong.vic.gov.au into
scraped/vic/maribyrnong/<address-slug>/raw/. Runs 8 apps in parallel."""
from __future__ import annotations

import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

from curl_cffi import requests
from bs4 import BeautifulSoup

BASE = "https://www.maribyrnong.vic.gov.au"
LISTING = f"{BASE}/Building-and-Planning/Advertised-Planning-Applications"
ROOT = Path(__file__).resolve().parent.parent
DEST_ROOT = ROOT / "scraped" / "vic" / "maribyrnong"
WORKERS = 8

_thread_local = threading.local()


def _session():
    """Per-thread curl_cffi Session. Isolation avoids cross-thread state
    issues in curl_cffi's connection pool."""
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session(impersonate="chrome131")
        _thread_local.session = s
    return s


def polite_get(url: str, *, referer: str | None = None):
    h = {"Sec-Fetch-Site": "same-origin" if referer else "none"}
    if referer:
        h["Referer"] = referer
    r = _session().get(url, headers=h, timeout=180)
    r.raise_for_status()
    return r


def list_application_urls() -> list[str]:
    soup = BeautifulSoup(polite_get(LISTING).content, "lxml")
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


def slug_from(detail_url: str) -> str:
    return detail_url.rstrip("/").rsplit("/", 1)[-1]


def safe_filename(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def download_app(idx: int, total: int, app: str) -> tuple[int, int]:
    """Worker: fetch one app's detail page + download every PDF on it."""
    slug = slug_from(app)
    raw_dir = DEST_ROOT / slug / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (DEST_ROOT / slug / "extracted").mkdir(parents=True, exist_ok=True)
    pdfs = pdf_links_on(app)
    print(f"[{idx}/{total}] {slug}  ({len(pdfs)} PDF(s))")
    app_bytes = 0
    for pdf_url in pdfs:
        r = _session().get(
            pdf_url,
            headers={"Sec-Fetch-Site": "same-origin", "Referer": app},
            timeout=180,
        )
        r.raise_for_status()
        dest = raw_dir / safe_filename(pdf_url)
        dest.write_bytes(r.content)
        size = dest.stat().st_size
        app_bytes += size
        print(f"    {size:>10,} B  {slug}/raw/{dest.name}")
    return len(pdfs), app_bytes


def main() -> int:
    print(f"Fetching listing: {LISTING}")
    apps = list_application_urls()
    n = len(apps)
    print(f"  found {n} application detail pages\n")

    total_pdfs = 0
    total_bytes = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = [
            ex.submit(download_app, i, n, app)
            for i, app in enumerate(apps, 1)
        ]
        for f in futures:
            n_pdfs, n_bytes = f.result()
            total_pdfs += n_pdfs
            total_bytes += n_bytes

    print(f"\nDone. {total_pdfs} PDF(s), {total_bytes:,} bytes total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
