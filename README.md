# GatePrep - Official GATE CS monitor and candidate extractor

This repository powers the scheduled GATE CS source-monitoring and question-candidate extraction stage for GatePrep.

## Pipeline

1. Checks official GATE index pages twice a week and on manual dispatch.
2. Records CS paper and answer-key links and their file metadata.
3. Downloads only allow-listed HTTPS paper/key PDFs.
4. Extracts selectable text from question papers and tries to parse matching rows from official answer-key PDFs.
5. Pairs questions and key rows by year, paper code, and printed question number.
6. Writes \`data/extraction-queue.json\` with every extracted candidate marked \`origin: official-pyq\` and \`verificationStatus: needs-review\`.

## Important limits

**This pipeline does not auto-verify questions.** PDF extraction can misread formulas, diagrams, page layouts, or options. Pairing a paper with an official key row is useful provenance but does not prove the statement or answer is correctly mapped. Every candidate needs human review against the original master paper and matching official key before being marked verified.

Papers without a unique matching key are skipped and listed in the extraction report. Solutions and memory tips are left blank rather than fabricated. Scanned/image-only content may not be extractable and is flagged for review.

## Sources monitored

- GATE 2026 historical downloads - IIT Guwahati: https://gate2026.iitg.ac.in/download.html
- GATE 2026 master question papers and answer keys - IIT Guwahati: https://gate2026.iitg.ac.in/QPs-answer-keys.html

The source index lists the historical bulk archive link for 2007-2025. The scanner records that link but does not automatically crawl its Google Drive folder contents.

## Outputs

- \`data/source-index.json\` - official source links and file metadata
- \`data/sync-report.json\` - source scan summary and changes
- \`data/extraction-queue.json\` - extracted candidate questions, all pending review
- \`data/extraction-report.json\` - matched pairs, skipped papers, failures, and warning counts

## Run locally

\`\`\`bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/sync_official_sources.py
python scripts/extract_cs_questions.py
\`\`\`

GitHub Actions is scheduled Monday and Thursday at 07:47 IST. Generated JSON is committed to \`main\`, so the app can fetch the latest review candidates over GitHub raw content. No repository secrets or API keys are required.
