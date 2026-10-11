# GatePrep — Official GATE CS Source Monitor

This repository powers the first scheduled source-monitoring step for GatePrep.

## What it does

- Checks official GATE index pages on a schedule and on manual dispatch.
- Finds links that look like GATE CS papers, CS answer keys, or bulk previous-paper archives.
- Probes file metadata (HTTP status, ETag, Last-Modified, and Content-Length) without downloading full PDF bodies.
- Writes `data/source-index.json` for an app to read, and `data/sync-report.json` with new, changed, removed, and failed-source details.
- Preserves the last known document links for a source when that official page temporarily fails.

## What it does not do yet

This is a **source-link and metadata monitor**, not a question extractor. It does not download or rehost the papers, parse PDF questions, extract answer keys, or mark any question verified. GATE 2025's official page explicitly notes the matching master-paper/answer-key relationship, so answer-key validation must use the correct pair. Candidate extraction and review is a separate next phase.

## Sources configured

- GATE 2026 historical downloads — IIT Guwahati: https://gate2026.iitg.ac.in/download.html
- GATE 2026 master papers and answer keys — IIT Guwahati: https://gate2026.iitg.ac.in/QPs-answer-keys.html
- GATE 2025 historical downloads — IIT Roorkee: https://gate2025.iitr.ac.in/download.html
- GATE 2025 master papers and final answer keys — IIT Roorkee: https://gate2025.iitr.ac.in/question-papers.html

The scanner only follows source pages from the HTTPS host allow-list in `sources.json`. It does not follow redirects when probing PDF metadata.

## Automation

The GitHub Actions workflow runs twice a week and can also be started manually from **Actions → GatePrep official-source monitor → Run workflow**. The first scheduled runs need no API key or repository secret. The workflow commits generated JSON back to `main` if it changes and uploads a report artifact for each run.

## Files

- `sources.json` — allow-listed official index URLs
- `scripts/sync_official_sources.py` — scanner and diff report
- `tests/test_sync.py` — parser, source-host, and change-detection tests
- `data/source-index.json` — latest checked links and metadata (created by first run)
- `data/sync-report.json` — latest run summary (created by first run)

## Running locally

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/sync_official_sources.py
```

Source page access is subject to each official site's availability and terms. This repo records links and metadata; it does not redistribute the source PDFs.
