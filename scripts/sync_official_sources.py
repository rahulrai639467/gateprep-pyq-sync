#!/usr/bin/env python3
"""Monitor official GATE CS paper/key links and publish a metadata index.

This script tracks source pages and official-hosted file metadata only. It does
not extract question statements or answer keys, and it never marks questions
as verified. Those steps require a separate extraction and human review stage.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "sources.json"
INDEX_PATH = ROOT / "data" / "source-index.json"
REPORT_PATH = ROOT / "data" / "sync-report.json"
USER_AGENT = "GatePrep-Official-Source-Monitor/1.0 (+https://github.com/rahulrai639467/gateprep-pyq-sync)"
FILE_SUFFIXES = {".pdf", ".zip", ".rar", ".7z"}
CS_PATTERN = re.compile(r"(?<![A-Za-z0-9])CS(?:[-_ ]?[12])?(?![A-Za-z0-9])", re.I)
YEAR_PATTERN = re.compile(r"\b(200[7-9]|201\d|202[0-6])\b")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return ""
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "/",
            parts.query,
            "",
        )
    )


def host_allowed(url: str, allowed_hosts: list[str]) -> bool:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        return (
            parts.scheme.lower() == "https"
            and any(host == item or host.endswith("." + item) for item in allowed_hosts)
        )
    except ValueError:
        return False


class AnchorParser(HTMLParser):
    """Small standard-library HTML anchor collector; no page scripts are run."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._anchor: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            attributes = dict(attrs)
            self._anchor = {"href": attributes.get("href") or "", "text": []}

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._anchor["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._anchor is not None:
            self.links.append(
                {
                    "href": str(self._anchor["href"]),
                    "text": " ".join(" ".join(self._anchor["text"]).split()),
                }
            )
            self._anchor = None


def parse_links(html: str, base_url: str) -> list[dict[str, str]]:
    parser = AnchorParser()
    parser.feed(html)
    parser.close()
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parser.links:
        absolute = normalized_url(urljoin(base_url, item["href"]))
        if not absolute or absolute in seen:
            continue
        if urlsplit(absolute).scheme != "https":
            continue
        seen.add(absolute)
        result.append({"url": absolute, "text": item["text"]})
    return result


def classify_document(title: str, url: str) -> str:
    """Classify by visible link text and full URL path, not just the filename."""
    path = urlsplit(url).path
    text = (title + " " + path).lower()
    compact = text.replace("_", " ").replace("-", " ")
    if re.search(r"answer[\s_-]*keys?|final[\s_-]*keys?|key[\s]*/[\s]*range|(?:^|[/_. -])keys?(?:[/_. -]|[0-9]|$)", text):
        return "answer_key"
    if "bulk" in compact and ("paper" in compact or "archive" in compact):
        return "bulk_archive"
    suffix = Path(path).suffix.lower()
    if suffix in {".zip", ".rar", ".7z"}:
        return "bulk_archive" if "bulk" in compact or "all papers" in compact else "archive"
    if "computer science" in compact or "information technology" in compact or CS_PATTERN.search(compact):
        return "question_paper_or_cs_resource"
    if "question paper" in compact or re.search(r"\bqp\b", compact):
        return "question_paper_or_cs_resource"
    return "official_file_other"


def is_candidate_file(title: str, url: str) -> bool:
    path = urlsplit(url).path.lower()
    suffix = Path(path).suffix.lower()
    combined = title + " " + path
    if suffix in FILE_SUFFIXES and (
        CS_PATTERN.search(combined)
        or "computer science" in combined.lower()
        or "information technology" in combined.lower()
        or ("bulk" in combined.lower() and "paper" in combined.lower())
    ):
        return True
    # Some pages use a download endpoint whose path has no .pdf suffix.
    return "bulk download" in title.lower() and "question paper" in title.lower()


def probe_file(session: requests.Session, url: str) -> dict[str, Any]:
    """Read metadata without downloading the PDF/archive body."""
    result: dict[str, Any] = {
        "http_status": None,
        "etag": None,
        "last_modified": None,
        "content_length": None,
        "probe_note": "",
    }
    try:
        response = session.head(url, timeout=(8, 18), allow_redirects=False)
        result["http_status"] = response.status_code
        result["etag"] = response.headers.get("ETag")
        result["last_modified"] = response.headers.get("Last-Modified")
        raw_length = response.headers.get("Content-Length")
        result["content_length"] = int(raw_length) if raw_length and raw_length.isdigit() else None
        if response.status_code in {405, 501}:
            response.close()
            response = session.get(
                url,
                headers={"Range": "bytes=0-0"},
                timeout=(8, 18),
                allow_redirects=False,
                stream=True,
            )
            result["http_status"] = response.status_code
            result["etag"] = response.headers.get("ETag")
            result["last_modified"] = response.headers.get("Last-Modified")
            raw_length = response.headers.get("Content-Length")
            result["content_length"] = int(raw_length) if raw_length and raw_length.isdigit() else None
            result["probe_note"] = "Used a range request because HEAD is unsupported."
            response.close()
        elif 300 <= response.status_code < 400:
            result["probe_note"] = "Redirect not followed; cross-host redirects are intentionally not fetched."
            response.close()
        else:
            response.close()
    except requests.RequestException as error:
        result["probe_note"] = f"Metadata probe failed: {type(error).__name__}: {error}"
    return result


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def metadata_changed(old: dict[str, Any], new: dict[str, Any]) -> bool:
    # Transport metadata is compared only when both runs supplied it.
    for key in ("etag", "last_modified", "content_length"):
        if old.get(key) not in (None, "") and new.get(key) not in (None, ""):
            if old.get(key) != new.get(key):
                return True
    # Classification changes (including newly inferred years) are real metadata changes.
    for key in ("document_type", "year_hint", "year_range", "title"):
        if key in old and old.get(key) != new.get(key):
            return True
    return False


def scan_source(
    session: requests.Session, source: dict[str, Any], allowed_hosts: list[str], document_hosts: list[str], previous_docs: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_url = source["index_url"]
    started = utc_now()
    if not host_allowed(source_url, allowed_hosts):
        return (
            {
                "source_id": source["id"],
                "label": source["label"],
                "index_url": source_url,
                "status": "rejected",
                "checked_at": started,
                "error": "Source index URL is not HTTPS on an explicitly allowed official host.",
                "document_count": 0,
            },
            [],
        )

    try:
        response = session.get(source_url, timeout=(10, 35), allow_redirects=True)
        response.raise_for_status()
        final_url = normalized_url(response.url)
        if not host_allowed(final_url, allowed_hosts):
            raise ValueError("Index page redirected outside the configured official host allow-list.")
        html = response.text
        page_hash = sha256_text(html)
        links = parse_links(html, final_url)
        downloads: list[dict[str, str]] = []
        for link in links:
            if not host_allowed(link["url"], document_hosts):
                continue
            if not is_candidate_file(link["text"], link["url"]):
                continue
            downloads.append(link)

        old_by_url = {doc.get("url"): doc for doc in previous_docs if doc.get("source_id") == source["id"]}
        now = utc_now()
        documents: list[dict[str, Any]] = []
        for link in downloads[:250]:
            url = link["url"]
            old = old_by_url.get(url, {})
            title = link["text"] or urlsplit(url).path.rsplit("/", 1)[-1] or url
            doc: dict[str, Any] = {
                "id": sha256_text(source["id"] + "|" + url)[:20],
                "source_id": source["id"],
                "source_label": source["label"],
                "title": title,
                "url": url,
                "host": urlsplit(url).hostname,
                "document_type": classify_document(title, url),
                "year_hint": None,
                "year_range": None,
                "first_seen_at": old.get("first_seen_at", now),
                "last_seen_at": now,
                "stale": False,
            }
            path_and_title = title + " " + urlsplit(url).path
            range_match = re.search(r"(200[7-9]|201\d|202[0-6])\s*(?:to|-|–)\s*(200[7-9]|201\d|202[0-6])", path_and_title, re.I)
            year_match = re.search(r"(?<!\d)(200[7-9]|201\d|202[0-6])(?!\d)", urlsplit(url).path)
            if year_match is None:
                year_match = YEAR_PATTERN.search(title)
            doc["year_hint"] = None if range_match else (int(year_match.group(1)) if year_match else None)
            if range_match:
                doc["year_range"] = {"start": int(range_match.group(1)), "end": int(range_match.group(2))}
            doc.update(probe_file(session, url))
            documents.append(doc)

        page_changed = bool(source.get("previous_page_sha256") and source["previous_page_sha256"] != page_hash)
        result = {
            "source_id": source["id"],
            "label": source["label"],
            "organizing_institute": source.get("organizing_institute"),
            "index_url": source_url,
            "final_url": final_url,
            "status": "ok",
            "http_status": response.status_code,
            "checked_at": now,
            "page_sha256": page_hash,
            "page_changed_since_previous_check": page_changed,
            "candidate_link_count": len(downloads),
            "document_count": len(documents),
            "truncated_at_250_candidates": len(downloads) > 250,
        }
        response.close()
        return result, documents
    except (requests.RequestException, ValueError) as error:
        previous = [
            {**doc, "stale": True}
            for doc in previous_docs
            if doc.get("source_id") == source["id"]
        ]
        return (
            {
                "source_id": source["id"],
                "label": source["label"],
                "organizing_institute": source.get("organizing_institute"),
                "index_url": source_url,
                "status": "error",
                "checked_at": utc_now(),
                "error": f"{type(error).__name__}: {error}",
                "document_count": len(previous),
                "stale_documents_retained": bool(previous),
            },
            previous,
        )


def main() -> int:
    config = load_json(SOURCES_PATH, {})
    if not config.get("official_sources"):
        print(f"::error::No official sources configured in {SOURCES_PATH}")
        return 2

    sources = config["official_sources"]
    allowed_hosts = [str(host).lower() for host in config.get("allowed_hosts", [])]
    document_hosts = [str(host).lower() for host in config.get("allowed_document_hosts", allowed_hosts)]
    for source in sources:
        host = (urlsplit(source.get("index_url", "")).hostname or "").lower()
        if host and host not in allowed_hosts:
            print(f"::error::Source {source.get('id')} uses an unapproved host: {host}")
            return 2

    previous = load_json(INDEX_PATH, {})
    previous_docs = previous.get("documents", []) if isinstance(previous, dict) else []
    previous_by_id = {doc.get("id"): doc for doc in previous_docs}
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})

    results: list[dict[str, Any]] = []
    current_docs: list[dict[str, Any]] = []
    ok_source_ids: set[str] = set()
    for source in sources:
        prior_result = next(
            (item for item in previous.get("source_results", []) if item.get("source_id") == source["id"]),
            {},
        )
        source_for_scan = {**source, "previous_page_sha256": prior_result.get("page_sha256")}
        result, documents = scan_source(session, source_for_scan, allowed_hosts, document_hosts, previous_docs)
        results.append(result)
        current_docs.extend(documents)
        if result["status"] == "ok":
            ok_source_ids.add(source["id"])
        else:
            print(f"::warning::Source scan failed for {source['id']}: {result.get('error', 'unknown error')}")

    # Detect new/changed links only within sources that were successfully scanned.
    new_documents: list[dict[str, Any]] = []
    changed_documents: list[dict[str, Any]] = []
    current_keys = {(doc.get("source_id"), doc.get("url")) for doc in current_docs}
    for doc in current_docs:
        old = previous_by_id.get(doc["id"])
        if old is None:
            if doc.get("source_id") in ok_source_ids:
                new_documents.append(doc)
        elif doc.get("source_id") in ok_source_ids and metadata_changed(old, doc):
            changed_documents.append(doc)

    removed_documents = []
    for old in previous_docs:
        source_id = old.get("source_id")
        if source_id in ok_source_ids and (source_id, old.get("url")) not in current_keys:
            removed_documents.append(
                {
                    "id": old.get("id"),
                    "source_id": source_id,
                    "title": old.get("title"),
                    "url": old.get("url"),
                    "detected_at": utc_now(),
                }
            )

    current_docs.sort(key=lambda item: (str(item.get("source_id")), str(item.get("year_hint") or ""), item.get("document_type", ""), item.get("title", "").lower()))
    failures = sum(1 for item in results if item["status"] != "ok")
    checked_at = utc_now()
    index_payload = {
        "schema_version": 1,
        "project": "GatePrep",
        "scope": "Official GATE CS source-link and file-metadata monitoring only; no question text or answer key has been extracted or approved.",
        "checked_at": checked_at,
        "overall_status": "ok" if failures == 0 else ("degraded" if failures < len(results) else "failed"),
        "source_results": results,
        "documents": current_docs,
        "change_summary": {
            "new_documents": new_documents,
            "changed_documents": changed_documents,
            "removed_documents": removed_documents,
        },
        "totals": {
            "sources_attempted": len(sources),
            "sources_ok": len(sources) - failures,
            "sources_failed": failures,
            "candidate_documents": len(current_docs),
            "new_documents": len(new_documents),
            "changed_documents": len(changed_documents),
            "removed_documents": len(removed_documents),
        },
    }
    report_payload = {
        "schema_version": 1,
        "checked_at": checked_at,
        "overall_status": index_payload["overall_status"],
        "note": "This report tracks official source pages and file metadata. It does not contain question text and does not automatically verify question answers.",
        "totals": index_payload["totals"],
        "failed_sources": [
            {"source_id": item["source_id"], "index_url": item["index_url"], "error": item.get("error")}
            for item in results if item["status"] != "ok"
        ],
        "new_documents": new_documents,
        "changed_documents": changed_documents,
        "removed_documents": removed_documents,
    }
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT_PATH.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(index_payload["totals"], indent=2))
    if failures:
        print(f"::warning::{failures} of {len(sources)} official source pages could not be checked. See data/sync-report.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
