#!/usr/bin/env python3
"""Extract GATE CS question candidates from paired official papers and answer keys.

All extracted records remain needs-review. Matching an official key row is
provenance metadata, not proof that text/options/answers have been parsed correctly.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import fitz
import pdfplumber
import requests

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "data" / "source-index.json"
SOURCES_PATH = ROOT / "sources.json"
QUEUE_PATH = ROOT / "data" / "extraction-queue.json"
REPORT_PATH = ROOT / "data" / "extraction-report.json"
USER_AGENT = "GatePrep-PYQ-Candidate-Extractor/1.0 (+https://github.com/rahulrai639467/gateprep-pyq-sync)"
MAX_PDF_BYTES = 25 * 1024 * 1024
YEAR_PATTERN = re.compile(r"(?<!\d)(200[7-9]|201\d|202[0-6])(?!\d)")
QUESTION_MARKER = re.compile(r"(?<![A-Za-z0-9])Q\.\s*(\d{1,2})(?!\s*[-–—]\s*Q\.)", re.I)
OPTION_MARKER = re.compile(r"(?<![A-Za-z0-9])\(([A-D])\)\s*")
KEY_ROW_PATTERN = re.compile(
    r"^\s*(\d{1,2})\s+(\d+)\s+(MCQ|MSQ|NAT)\s+(GA|CS(?:-?\d)?)\s+(.+?)\s+([12])\s*$",
    re.I,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def normalized(value: str) -> str:
    return " ".join((value or "").replace("\u00a0", " ").split())


def trusted_https(url: str, hosts: list[str]) -> bool:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        return parts.scheme.lower() == "https" and any(
            host == allowed or host.endswith("." + allowed) for allowed in hosts
        )
    except ValueError:
        return False


def infer_year(doc: dict[str, Any]) -> int | None:
    if isinstance(doc.get("year_hint"), int):
        return doc["year_hint"]
    match = YEAR_PATTERN.search((doc.get("title") or "") + " " + (doc.get("url") or ""))
    return int(match.group(1)) if match else None


def infer_paper_code(doc: dict[str, Any]) -> str:
    blob = ((doc.get("title") or "") + " " + urlsplit(doc.get("url") or "").path).lower()
    # Official names include CS1.pdf, CS12025.pdf, and CS124S5.pdf.
    if re.search(r"cs[\s_-]*1(?=[\s_.\-/]|20\d{2}|24s|25|26|$)", blob):
        return "CS-1"
    if re.search(r"cs[\s_-]*2(?=[\s_.\-/]|20\d{2}|24s|25|26|$)", blob):
        return "CS-2"
    return "CS"


def download_pdf(session: requests.Session, url: str, hosts: list[str]) -> bytes:
    if not trusted_https(url, hosts):
        raise ValueError("PDF URL is not HTTPS on the configured official-document allow-list.")
    response = session.get(url, timeout=(10, 45), allow_redirects=False, stream=True)
    try:
        if response.status_code in {301, 302, 303, 307, 308}:
            raise ValueError("Redirect not followed while fetching an official PDF.")
        response.raise_for_status()
        raw = bytearray()
        for chunk in response.iter_content(1024 * 128):
            if chunk:
                raw.extend(chunk)
                if len(raw) > MAX_PDF_BYTES:
                    raise ValueError("PDF exceeds the 25 MB safety limit.")
        data = bytes(raw)
        if not data.startswith(b"%PDF-"):
            raise ValueError("Response body does not have a PDF file signature.")
        return data
    finally:
        response.close()


def text_from_pdf(pdf_bytes: bytes) -> str:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
        return "\n".join(page.get_text("text", sort=True) for page in document)


def clean_question_text(text: str) -> str:
    output: list[str] = []
    for line in text.splitlines():
        line = normalized(line)
        if not line:
            continue
        if re.search(r"Organizing Institute\s*:?\s*IIT Guwahati", line, re.I):
            continue
        if re.search(r"Page\s+\d+\s+of\s+\d+", line, re.I):
            continue
        if re.search(r"Computer Science\s*(?:&|and)\s*Information Technology\s*\(CS[- ]?\d\)", line, re.I):
            continue
        if line.lower() in {"general aptitude (ga)", "computer science & information technology"}:
            continue
        output.append(line)
    return "\n".join(output).strip()


def split_questions(text: str) -> dict[int, str]:
    """Split on printed question labels, ignoring banners such as 'Q.1–Q.5 Carry...'."""
    accepted: list[re.Match[str]] = []
    for match in QUESTION_MARKER.finditer(text):
        tail = text[match.end():match.end() + 100]
        if re.match(r"\s*[–—-]\s*Q\.\s*\d{1,2}\s+Carry\b", tail, re.I):
            continue
        accepted.append(match)

    result: dict[int, str] = {}
    for index, match in enumerate(accepted):
        number = int(match.group(1))
        end = accepted[index + 1].start() if index + 1 < len(accepted) else len(text)
        body = clean_question_text(text[match.end():end])
        if body and number not in result:
            result[number] = body
    return result


def parse_question_body(body: str) -> tuple[str, list[dict[str, str]], list[str]]:
    matches = list(OPTION_MARKER.finditer(body))
    chosen: list[re.Match[str]] = []
    option_start: int | None = None
    for i, match in enumerate(matches):
        sequence = matches[i:i + 4]
        if match.group(1) == "A" and len(sequence) == 4 and [m.group(1) for m in sequence] == ["A", "B", "C", "D"]:
            option_start = match.start()
            chosen = sequence
            break
    if option_start is None:
        return normalized(body.replace("\n", " ")), [], [
            "Options were not reliably detected; inspect the original PDF."
        ]

    stem = normalized(body[:option_start].replace("\n", " "))
    options: list[dict[str, str]] = []
    warnings: list[str] = []
    for i, match in enumerate(chosen):
        start = match.end()
        end = chosen[i + 1].start() if i + 1 < len(chosen) else len(body)
        option_text = normalized(body[start:end].replace("\n", " "))
        if not option_text:
            option_text = "[Option content may be embedded as an image; inspect the original PDF.]"
            warnings.append("At least one option may contain visual content not captured by text extraction.")
        options.append({"id": match.group(1), "text": option_text})
    return stem, options, warnings


def parse_answer_key_text(text: str) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for line in text.splitlines():
        match = KEY_ROW_PATTERN.match(normalized(line))
        if not match:
            continue
        number, session, qtype, section, key_range, marks = match.groups()
        result[int(number)] = {
            "questionNumber": int(number),
            "session": str(session),
            "questionType": qtype.upper(),
            "section": section,
            "keyRange": normalized(key_range),
            "marks": float(marks),
        }
    return result


def parse_answer_key(pdf_bytes: bytes) -> dict[int, dict[str, Any]]:
    """Prefer table extraction; fall back to line-oriented PDF text."""
    result: dict[int, dict[str, Any]] = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for row in table or []:
                    cells = [normalized(cell or "") for cell in row]
                    if len(cells) < 6 or not cells[0].isdigit() or not cells[1].isdigit():
                        continue
                    qtype, marks = cells[2].upper(), cells[-1]
                    if qtype not in {"MCQ", "MSQ", "NAT"} or marks not in {"1", "2"}:
                        continue
                    result[int(cells[0])] = {
                        "questionNumber": int(cells[0]),
                        "session": cells[1],
                        "questionType": qtype,
                        "section": cells[3],
                        "keyRange": cells[4],
                        "marks": float(marks),
                    }
    return result if result else parse_answer_key_text(text_from_pdf(pdf_bytes))


def key_answer_value(row: dict[str, Any]) -> str | list[str] | float:
    value = normalized(str(row.get("keyRange") or ""))
    qtype = row.get("questionType")
    if qtype == "MSQ":
        return [item for item in re.split(r"[;,]\s*", value) if item]
    if qtype == "MCQ":
        return value.upper()
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*(?:to|-)\s*(-?\d+(?:\.\d+)?)\s*", value, re.I)
    if match:
        low, high = float(match.group(1)), float(match.group(2))
        if low == high:
            return int(low) if low.is_integer() else low
        return f"{low:g} to {high:g}"
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except ValueError:
        return value


def pair_papers_and_keys(
    documents: list[dict[str, Any]], document_hosts: list[str]
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]]]:
    papers: list[dict[str, Any]] = []
    keys: list[dict[str, Any]] = []
    for doc in documents:
        url = doc.get("url") or ""
        if Path(urlsplit(url).path).suffix.lower() != ".pdf" or not trusted_https(url, document_hosts):
            continue
        year = infer_year(doc)
        if year is None or year < 2019 or year > 2026:
            continue
        record = {**doc, "_year": year, "_code": infer_paper_code(doc)}
        if doc.get("document_type") == "answer_key":
            keys.append(record)
        elif doc.get("document_type") == "question_paper_or_cs_resource":
            papers.append(record)

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    issues: list[dict[str, Any]] = []
    for paper in papers:
        matches = [key for key in keys if key["_year"] == paper["_year"] and key["_code"] == paper["_code"]]
        if len(matches) == 1:
            pairs.append((paper, matches[0]))
        elif not matches:
            issues.append({
                "year": paper["_year"], "paperCode": paper["_code"], "paperUrl": paper["url"],
                "issue": "No unique matching official answer-key PDF; skipped rather than guessing answers."
            })
        else:
            issues.append({
                "year": paper["_year"], "paperCode": paper["_code"], "paperUrl": paper["url"],
                "issue": "Multiple possible answer-key PDFs; needs human pairing."
            })
    return pairs, issues


def parse_question_paper(pdf_bytes: bytes) -> dict[int, dict[str, Any]]:
    questions = split_questions(text_from_pdf(pdf_bytes))
    result: dict[int, dict[str, Any]] = {}
    for number, body in questions.items():
        stem, options, warnings = parse_question_body(body)
        if len(stem) < 8:
            warnings.append("Question stem is short or mostly visual; inspect the source PDF.")
        if stem:
            result[number] = {"statement": stem, "options": options, "extractionWarnings": warnings}
    return result


def extract_candidates(index: dict[str, Any], sources: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    documents = index.get("documents") or []
    document_hosts = [str(host).lower() for host in sources.get("allowed_document_hosts", sources.get("allowed_hosts", []))]
    pairs, pairing_issues = pair_papers_and_keys(documents, document_hosts)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8"})
    candidates: list[dict[str, Any]] = []
    pair_reports: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for paper, key in pairs:
        year, code = paper["_year"], paper["_code"]
        pair_label = f"{year} {code}"
        try:
            paper_bytes = download_pdf(session, paper["url"], document_hosts)
            key_bytes = download_pdf(session, key["url"], document_hosts)
            questions = parse_question_paper(paper_bytes)
            key_rows = parse_answer_key(key_bytes)
            matched = 0
            without_key: list[int] = []
            for number, question in sorted(questions.items()):
                row = key_rows.get(number)
                if not row:
                    without_key.append(number)
                    continue
                answer = key_answer_value(row)
                qtype = row["questionType"]
                warnings = list(question["extractionWarnings"])
                if qtype in {"MCQ", "MSQ"} and len(question["options"]) != 4:
                    warnings.append("MCQ/MSQ options could not be fully extracted; compare with the PDF.")
                if qtype == "NAT" and isinstance(answer, str) and " to " in answer:
                    warnings.append("NAT key is a range; numeric range scoring still needs validation.")
                question_id = f"gate-{year}-{code.lower().replace('-', '')}-q{number:02d}"
                topic = "General Aptitude" if number <= 10 else "Needs classification"
                reference = (
                    f"Automated candidate from official master paper ({pair_label}), Q.{number}; "
                    f"paired with official answer-key row {number} (session {row['session']}). "
                    "Human review required: inspect statement, formulas, diagrams, options, and answer mapping."
                )
                candidates.append({
                    "id": question_id,
                    "origin": "official-pyq",
                    "verificationStatus": "needs-review",
                    "paperCode": code,
                    "year": year,
                    "session": row["session"],
                    "questionNumber": str(number),
                    "sourceUrl": paper["url"],
                    "answerKeySource": key["url"],
                    "sourceReference": reference,
                    "subject": "General Aptitude" if number <= 10 else "Computer Science & Information Technology",
                    "topic": topic,
                    "questionType": qtype,
                    "statement": question["statement"],
                    "options": question["options"],
                    "correctAnswer": answer,
                    "marks": row["marks"],
                    "negativeMarks": row["marks"] / 3 if qtype == "MCQ" else 0,
                    "solution": "",
                    "memoryTip": "",
                    "extractionWarnings": warnings,
                    "extractedAt": utc_now(),
                })
                matched += 1
            pair_reports.append({
                "year": year,
                "paperCode": code,
                "paperUrl": paper["url"],
                "answerKeyUrl": key["url"],
                "parsedQuestions": len(questions),
                "answerKeyRows": len(key_rows),
                "matchedCandidates": matched,
                "questionNumbersWithoutKeyRow": without_key,
                "status": "ok" if matched else "no-matches",
            })
        except Exception as error:
            failures.append({
                "pair": pair_label, "paperUrl": paper["url"], "answerKeyUrl": key["url"],
                "error": f"{type(error).__name__}: {error}"
            })

    unique = {item["id"]: item for item in candidates}
    candidates = sorted(unique.values(), key=lambda item: (item["year"], item["paperCode"], int(item["questionNumber"])))
    report = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "status": "ok" if not failures else "degraded",
        "note": "Candidate text and key mapping are not verified. Every record is kept in needs-review; no solution or memory tip is invented.",
        "pairs_attempted": len(pairs),
        "pairs": pair_reports,
        "candidate_count": len(candidates),
        "pairing_issues": pairing_issues,
        "failures": failures,
        "questions_with_extraction_warnings": sum(1 for item in candidates if item.get("extractionWarnings")),
    }
    return candidates, report


def main() -> int:
    index = load_json(INDEX_PATH, {})
    sources = load_json(SOURCES_PATH, {})
    candidates, report = extract_candidates(index, sources)
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps({
        "schemaVersion": 1,
        "generatedAt": utc_now(),
        "note": "Candidates only. Compare every question against the original master paper and matching official answer key before marking it verified.",
        "questions": candidates,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "candidate_count": report["candidate_count"],
        "pairs_attempted": report["pairs_attempted"],
        "pairs_succeeded": sum(1 for item in report["pairs"] if item["status"] == "ok"),
        "pairing_issues": len(report["pairing_issues"]),
        "failures": len(report["failures"]),
        "questions_with_extraction_warnings": report["questions_with_extraction_warnings"],
    }, indent=2))
    for failure in report["failures"]:
        print(f"::warning::Could not extract {failure.get('pair')}: {failure.get('error')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
