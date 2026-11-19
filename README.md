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
- Scanned PDFs / tables that are images are not read yet (no OCR or vision fallback).
- DOCX/XLSX have no real page numbers (page = 1 or sheet number).
- Gold evaluation files are user-provided and are not included in the repository; verify any gold matrix before treating it as authoritative.
- Free-tier Gemini may use inputs to improve Google products: avoid confidential tenders on it.
- This is a drafting aid. An engineer must review every row before anything goes to a customer.
