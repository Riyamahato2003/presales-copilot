# Pre-Sales Copilot

Reads a customer tender/spec and one or more OEM brochures/offers, and produces a **cited compliance
matrix** in Excel. Every row carries a status, the offered value, a verbatim quote and the PDF page.
Nothing is trained locally: the laptop only runs Python; Gemini does the language work via API.

## Setup (Windows/Mac/Linux, Python 3.11+)
```
python -m venv .venv
.venv\Scripts\activate          # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # Mac/Linux: cp .env.example .env   then put your key in .env
```
Get a key in Google AI Studio. Keep it only in `.env`. The default model is configurable with
`GEMINI_MODEL`; monitor AI Studio quota before running large tenders.

## Use

### Web application

```bash
uvicorn webapp:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, upload a tender and one or more manufacturer documents, then download the generated Excel matrix. Set `GEMINI_API_KEY` in `.env` first. Set `COPILOT_ACCESS_PASSWORD` to enable HTTP Basic authentication. For a hosted deployment, configure both values as private environment variables; never commit `.env`.

The included `render.yaml` is a deployment starting point for Render. Add a strong `COPILOT_ACCESS_PASSWORD` in the Render dashboard before use. The app persists job state and files on local disk and is intended for a single server process with persistent disk; it is not a Vercel serverless job system or a horizontally scaled deployment.

### CLI
```
python cli.py parse path/to/brochure.pdf                          # inspect chunks without API use
python cli.py run --tender path/to/tender.docx --brochure path/to/offer.pdf --out out/matrix.xlsx
python cli.py eval --results out/matrix.json --gold path/to/reviewer_gold.json
python -m pytest -q tests                                        # no API needed
```
Repeat `--brochure` to compare several products: one sheet per product plus a Summary sheet.
Set `EMBED_BACKEND=local` in `.env` to use offline embeddings and save API quota.

## How it works
1. `ingest.py` - PDF/DOCX/XLSX to page-tagged chunks. Letterheads removed, chunks follow headings,
   "not included / excluded" lists are *pinned* (always shown to the matcher).
2. `requirements_extract.py` - Gemini splits the tender into atomic, verbatim requirements (JSON).
3. `retrieve.py` - hybrid search (embeddings + keyword/number overlap) per requirement.
4. `match.py` - Gemini returns status, offered value, chunk id, verbatim quote, reasoning, confidence.
5. `verify.py` - code checks the quote, its numbers, numeric coverage of required values, source type,
   and confidence. Unsupported claims are downgraded. API failures and not-run rows have separate run states.
6. `export.py` - Excel with colour-coded status, reviewer-override dropdown, effective status and a
   "tender form entry" (YES / NO / REVIEW) driven by formulas.

## Known limitations
- Tests use generated synthetic PDF/DOCX documents and do not require customer files or a Gemini key.
- API quota exhaustion is recorded separately from compliance status; successful provider responses
  are cached and reused on reruns. Once quota is exhausted, remaining rows are marked not run.
- Selective local OCR supports image-only and hidden-text-layer scans with Tesseract. Mixed-layout image tables are not yet extracted. OCR evidence and tender requirements require human review; OCR is not independently accuracy-certified.
- DOCX and legacy DOC text has no verified physical page number; XLSX uses sheet index rather than page. Precise paragraph/cell citations are future work.
- Gold evaluation files are user-provided and are not included in the repository; verify any gold matrix before treating it as authoritative.
- Free-tier Gemini may use inputs to improve Google products: avoid confidential tenders on it.
- This is a drafting aid. An engineer must review every row before anything goes to a customer.

## V2 ingestion checkpoint

Install system tools for the new local paths: on Debian/Ubuntu, `sudo apt-get install tesseract-ocr tesseract-ocr-eng antiword`. On macOS, install Tesseract and antiword through an available package manager, or convert legacy DOC to DOCX locally. Set `COPILOT_OCR_LANG` to an installed Tesseract language code (default `eng`). Native PDFs need neither OCR nor a model call for parsing. The PDF reader limits documents to 150 pages and OCR to 40 pages, with bounded raster size and subprocess timeout. Failed OCR stops ingestion rather than silently omitting the page. Word and OCR text can contain transcription or layout errors. A full-page hidden text layer is re-read locally, but complex embedded image tables, multi-column order, and exact quote-level bounding boxes still need work.

### Private evaluation protocol

Keep original tender files, manufacturer files, supplier compliance sheets, extracted text, caches, and gold labels outside the public repository. Never send confidential or unapproved documents to a model provider. First run `python cli.py parse PATH` locally to inspect ingestion (this prints source excerpts locally); then compare independently reviewed requirements and evidence with the output of an authorized provider run. A supplier's self-declared compliance is a separate reference, not independent truth. Missing technical annexures or offer-specific commitments must be reported as missing evidence, not invented. Maintain a held-out test set and retain synthetic regression tests. Record citation accuracy, extraction coverage, false compliant decisions, abstentions, and run completion separately. Do not claim real-world compliance accuracy until this evaluation has been performed.

This branch is not a production deployment. The application still requires a private persistent backend and configured model access for end-to-end assessment. The existing Vercel build is not validated for persistent jobs or local OCR.

### V2 source provenance checkpoint

Native, unrotated PDF pages now carry normalized-line region matches when each line is unambiguous. Ruled tables detected by PyMuPDF contribute supplemental row chunks with ordered cells and row-level regions. Primary native text is preserved; supplemental rows are available for retrieval but not duplicated in the tender requirement-extraction prompt. Detected table rows require human review. OCR and rotated pages retain page-level references; precise OCR word coordinates and image-table cell recognition are not implemented. Word uses document-level references and spreadsheets use worksheet names, not invented physical page numbers.

JSON output carries requirement source references, supplier evidence type, source locators, and available whole-chunk/row regions. The workbook includes both tender and supplier source references. Untrusted document/model strings in matrix cells are exported as literal text rather than spreadsheet formulas; intended status formulas remain formulas. This is a provenance improvement, not an independently validated compliance or table-recognition accuracy claim. Ambiguous or unmatched requirement sources are flagged for review.
