"""SEC EDGAR collection client — legal, rate-limited, archived (Q3 / Q6).

合规设计：
- 所有请求携带可识别身份的 User-Agent（config.EDGAR_USER_AGENT，可用环境变量覆盖）；
- 全局请求间隔 ≥ config.EDGAR_REQUEST_INTERVAL_SECONDS（限速）；
- 仅对 5xx / 网络错误退避重试；遇到 403 / 429（访问控制 / 限流）立即终止并抛错；
- 不访问任何需要登录 / 付费墙 / 验证码的资源；
- 原始响应按 URL 哈希归档至 data/raw/，并写入 manifest.jsonl 供追溯。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import httpx

from . import config


class CollectError(RuntimeError):
    """Raised when a collection step must stop (compliance or network)."""


# ---------------------------------------------------------------------------
# HTML -> plain text
# ---------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)


def html_to_text(html_str: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html_str)
    except Exception:
        pass
    return re.sub(r"\s+", " ", " ".join(parser._parts)).strip()


# ---------------------------------------------------------------------------
# Filing reference
# ---------------------------------------------------------------------------
@dataclass
class FilingInfo:
    cik: str  # 10-digit zero-padded
    accession: str  # e.g. 0001045810-26-000021
    document: str  # primary document filename
    form: str  # 10-K / 20-F / ...
    filing_date: date | None
    report_date: date | None

    @property
    def url(self) -> str:
        return (
            f"https://www.sec.gov/Archives/edgar/data/{int(self.cik)}/"
            f"{self.accession.replace('-', '')}/{self.document}"
        )


# ---------------------------------------------------------------------------
# Rate-limited EDGAR client
# ---------------------------------------------------------------------------
class EdgarClient:
    def __init__(
        self,
        raw_dir: Path = config.RAW_DIR,
        user_agent: str = config.EDGAR_USER_AGENT,
        interval: float = config.EDGAR_REQUEST_INTERVAL_SECONDS,
        timeout: float = config.EDGAR_HTTP_TIMEOUT,
        max_retries: int = config.EDGAR_MAX_RETRIES,
        archive: bool = True,
    ) -> None:
        self.raw_dir = Path(raw_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.raw_dir / "manifest.jsonl"
        self.interval = interval
        self.max_retries = max_retries
        self.archive_enabled = archive
        self.request_count = 0
        self._client = httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json, text/html, */*",
            },
            timeout=timeout,
            follow_redirects=True,
        )
        self._last_request_ts = 0.0

    # -- compliance --------------------------------------------------------
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_request_ts = time.monotonic()
        self.request_count += 1

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "EdgarClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- fetch ---------------------------------------------------------------
    def get(self, url: str) -> str:
        """GET with rate limiting + retry policy. Raises CollectError on 403/429."""
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self._client.get(url)
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(2**attempt)
                continue
            if resp.status_code in (403, 429):
                # 合规红线：访问控制 / 限流 → 立即停止，绝不绕过
                raise CollectError(
                    f"EDGAR 返回 {resp.status_code}（访问控制/限流），已立即停止采集: {url}"
                )
            if resp.status_code >= 500:
                last_error = CollectError(f"EDGAR {resp.status_code} on {url}")
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            text = resp.text
            if self.archive_enabled:
                self._archive(url, text)
            return text
        raise CollectError(f"重试 {self.max_retries} 次后仍失败: {url} ({last_error})")

    def get_json(self, url: str) -> dict | list:
        return json.loads(self.get(url))

    def _archive(self, url: str, content: str) -> None:
        digest = hashlib.sha1(url.encode()).hexdigest()[:12]
        suffix = "json" if url.endswith(".json") else "htm"
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", url.rsplit("/", 1)[-1])[:60] or "index"
        path = self.raw_dir / f"{digest}-{name}.{suffix}"
        path.write_text(content, encoding="utf-8")
        with self.manifest_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "url": url,
                        "file": path.name,
                        "bytes": len(content.encode("utf-8")),
                        "archived_at": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


# ---------------------------------------------------------------------------
# EDGAR helpers
# ---------------------------------------------------------------------------
def latest_annual_filing(
    client: EdgarClient, cik: str, forms: tuple[str, ...] = ("10-K", "20-F")
) -> FilingInfo | None:
    """Latest annual report (10-K or 20-F) for a CIK, via the submissions API."""
    data = client.get_json(f"{config.DATA_SEC_GOV}/submissions/CIK{cik}.json")
    recent = data.get("filings", {}).get("recent", {})
    best: FilingInfo | None = None
    for form, accession, doc, filed, report_date in zip(
        recent.get("form", []),
        recent.get("accessionNumber", []),
        recent.get("primaryDocument", []),
        recent.get("filingDate", []),
        recent.get("reportDate", []),
    ):
        if form not in forms or not doc:
            continue
        info = FilingInfo(
            cik=cik,
            accession=accession,
            document=doc,
            form=form,
            filing_date=date.fromisoformat(filed) if filed else None,
            report_date=date.fromisoformat(report_date) if report_date else None,
        )
        if best is None or (info.filing_date or date.min) > (best.filing_date or date.min):
            best = info
    return best


def fts_search(
    client: EdgarClient,
    q: str,
    ciks: str | None = None,
    forms: str | None = None,
) -> list[dict]:
    """EDGAR full-text search. Returns hit dicts with keys: doc_id, cik,
    file_date, url, form, file_type."""
    params = [f"q=%22{q}%22"]
    if ciks:
        params.append(f"ciks={ciks}")
    if forms:
        params.append(f"forms={forms}")
    data = client.get_json(f"{config.EFTS_SEC_GOV}?{'&'.join(params)}")
    hits = []
    for h in data.get("hits", {}).get("hits", []):
        src = h["_source"]
        doc_id = h["_id"]
        accession, _, filename = doc_id.partition(":")
        hit_ciks = src.get("ciks", [])
        cik = next((c for c in hit_ciks if ciks and c == ciks), hit_ciks[0] if hit_ciks else "")
        file_date = src.get("file_date")
        hits.append(
            {
                "doc_id": doc_id,
                "accession": accession,
                "filename": filename,
                "cik": cik,
                "file_date": file_date,
                "form": src.get("file_type", ""),
                "is_exhibit": bool(re.match(r"ex\d", filename, re.I)),
                "url": (
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                    f"{accession.replace('-', '')}/{filename}"
                )
                if cik
                else "",
            }
        )
    return hits


def pick_recent_hit(hits: list[dict], min_date: date | None = None) -> dict | None:
    """Prefer primary (non-exhibit) documents, then the most recent filing."""
    if not hits:
        return None
    primary = [h for h in hits if not h["is_exhibit"] and h["form"] in ("10-K", "20-F", "10-Q")]
    pool = primary or hits
    if min_date:
        pool = [h for h in pool if h["file_date"] and date.fromisoformat(h["file_date"]) >= min_date]
        if not pool:
            return None
    return max(pool, key=lambda h: h["file_date"] or "")


def fetch_filing_text(client: EdgarClient, url: str) -> str:
    return html_to_text(client.get(url))


def find_mentions(
    text: str,
    term: str,
    max_hits: int = 2,
    window: int = 200,
    case_insensitive: bool = False,
) -> list[str]:
    """Context snippets around whole-word mentions of `term` in filing text."""
    flags = re.IGNORECASE if case_insensitive else 0
    snippets: list[str] = []
    for m in re.finditer(
        rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, flags
    ):
        s = max(0, m.start() - window)
        e = min(len(text), m.end() + window)
        snippet = text[s:e].strip()
        snippets.append(snippet)
        if len(snippets) >= max_hits:
            break
    return snippets
