# GatePrep — Official GATE CS Source Monitor

This repository powers the scheduled source-monitoring stage for GatePrep.

## What it does

- Checks the official GATE 2026 downloads page and official master-paper/answer-key index on a schedule and on manual dispatch.
- Finds links that look like GATE CS papers, CS answer keys, or the official bulk archive covering previous papers from 2007–2025.
- Probes file metadata (HTTP status, ETag, Last-Modified, and Content-Length) without downloading full PDF or archive bodies.
- Writes `data/source-index.json` for the app to read, and `data/sync-report.json` with new, changed, removed, and failed-source details.
- Preserves the last known document links for a source when that official index temporarily fails.
- Allows the bulk archive link only because it is linked from the official IIT Guwahati GATE 2026 download page; the scanner does not crawl arbitrary external links.

## What it does not do yet

This is a **source-link and metadata monitor**, not a question extractor. It does not download/rehost the papers, parse PDF questions, extract answer keys, or mark any question verified. GATE's official 2026 page warns that the answer key matches the master-paper question order, which may differ from the candidate console order. Any extracted data must use the matching master paper and associated answer key and remain pending human review.

## Sources configured

- GATE 2026 historical downloads — IIT Guwahati: https://gate2026.iitg.ac.in/download.html
- GATE 2026 master papers and answer keys — IIT Guwahati: https://gate2026.iitg.ac.in/QPs-answer-keys.html

The historical download page lists bulk papers from 2007–2025 and year-wise papers for 2019–2025. The scheduled job monitors the official index and any relevant links it exposes. A link being listed does not mean the PDF contents have been extracted or verified.

## Automation

The GitHub Actions workflow runs twice a week and can also be started manually from **Actions → GatePrep official-source monitor → Run workflow**. It requires no API key or repository secret. The workflow commits updated source metadata to `main` and uploads a report artifact for each run.

## Files

- `sources.json` — allow-listed official index and document hosts
- `scripts/sync_official_sources.py` — scanner and change report
- `tests/test_sync.py` — parser, URL-host, and classification tests
- `data/source-index.json` — latest scanned links and metadata
- `data/sync-report.json` — latest run summary

## Run locally

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/sync_official_sources.py
```

Source page access remains subject to each official site's availability and terms. This repo records links and metadata; it does not redistribute PDFs or answer keys.
