"""Thin EDGAR client: rate-limited GETs with an on-disk cache.

Everything downloaded lands under data/raw/ keyed by URL, so re-running the
pipeline never re-hits the SEC for a file it already has.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
CACHE = RAW / "http"


class Edgar:
    def __init__(self, user_agent: str, max_rps: float = 8):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self.min_gap = 1.0 / max_rps
        self._last = 0.0
        self._lock = threading.Lock()  # the rate limit holds across threads
        CACHE.mkdir(parents=True, exist_ok=True)

    def _path(self, url: str) -> Path:
        return CACHE / hashlib.sha1(url.encode()).hexdigest()

    def get(self, url: str, *, cache: bool = True) -> bytes:
        path = self._path(url)
        if cache and path.exists():
            return path.read_bytes()
        for attempt in range(5):
            with self._lock:
                wait = self.min_gap - (time.monotonic() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            resp = self.session.get(url, timeout=120)
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            if cache:
                path.write_bytes(resp.content)
            return resp.content
        resp.raise_for_status()
        return resp.content

    def get_text(self, url: str, **kw) -> str:
        return self.get(url, **kw).decode("utf-8", errors="replace")

    # ---- full-text search -------------------------------------------------
    def fts(self, query: str, forms: list[str], start: str, end: str,
            max_hits: int = 1000, ciks: list[str] | None = None) -> list[dict]:
        """EDGAR full-text search. Returns the `_source` of each hit plus `_id`.

        FTS pages 100 hits at a time and caps any query at 10,000 results.
        """
        hits: list[dict] = []
        offset = 0
        while offset < max_hits:
            params = {"q": query, "forms": ",".join(forms), "dateRange": "custom",
                      "startdt": start, "enddt": end, "from": offset}
            if ciks:
                params["ciks"] = ",".join(c.zfill(10) for c in ciks)
            url = "https://efts.sec.gov/LATEST/search-index?" + urlencode(params)
            data = json.loads(self.get_text(url))
            page = data.get("hits", {}).get("hits", [])
            for h in page:
                src = dict(h["_source"])
                src["_id"] = h["_id"]
                hits.append(src)
            total = data.get("hits", {}).get("total", {}).get("value", 0)
            offset += len(page)
            if not page or offset >= total:
                break
        return hits[:max_hits]

    @staticmethod
    def doc_url(hit: dict) -> str:
        """Archive URL for one FTS hit (`_id` is '<adsh>:<filename>')."""
        adsh, fname = hit["_id"].split(":", 1)
        cik = str(int(hit["ciks"][0]))
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{fname}"


def load_form_index(edgar: Edgar, quarter: str) -> list[dict]:
    """Parse EDGAR's fixed-width full-index/form.idx for a quarter."""
    text = edgar.get_text(f"https://www.sec.gov/Archives/edgar/full-index/{quarter}/form.idx")
    rows = []
    started = False
    for line in text.splitlines():
        if line.startswith("-----"):
            started = True
            continue
        if not started or not line.strip():
            continue
        # Columns: form type, company name, CIK, date filed, file name.
        parts = line.rsplit(None, 3)
        if len(parts) != 4:
            continue
        head, cik, date, fname = parts
        form = head[:17].strip()
        company = head[17:].strip()
        rows.append({"form": form, "company": company, "cik": cik, "date": date, "path": fname})
    return rows
