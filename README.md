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
Get a key in Google AI Studio. Keep it only in `.env`. Check AI Studio for current model names and
free-tier limits and edit `GEMINI_MODEL` / `MIN_INTERVAL_SEC` if you see 429 errors.

## Use

### Web application

```bash
uvicorn webapp:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, upload a tender and one or more manufacturer documents, then download the generated Excel matrix. Set `GEMINI_API_KEY` in `.env` first. For a hosted deployment, configure that key as a private environment variable in the hosting provider; never commit `.env`.

The included `render.yaml` is a deployment starting point for Render. It does not provision a database, persistent storage, or authentication. Treat an internet-facing deployment as a demo until access controls and durable job storage are configured.

### CLI
```
python cli.py parse data/brochures/BCSA_SP160_offer.pdf          # see how a file was chunked (no API)
python cli.py run --tender data/tenders/axial_compressor_gearbox_tender.docx ^
                  --brochure data/brochures/BCSA_SP160_offer.pdf --out out/axial.xlsx
python cli.py eval --results out/axial.json --gold data/gold/axial_compressor_gold.json
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
5. `verify.py` - code checks the answer: the quote must exist in the cited chunk, must not just echo the
   tender, numbers in the offered value must appear in the chunk. Failures become "Needs human review".
6. `export.py` - Excel with colour-coded status, reviewer-override dropdown, effective status and a
   "tender form entry" (YES / NO / REVIEW) driven by formulas.

## Known limitations
- Verified here: parsing, verification, export, evaluation, and the pipeline end to end with a stub model.
  NOT verified here: live Gemini calls (no access from the build environment). Expect to fix small
  SDK/model-name issues on first run and send the error text back.
- Scanned PDFs / tables that are images are not read yet (no OCR or vision fallback).
- DOCX/XLSX have no real page numbers (page = 1 or sheet number).
- `data/gold/*.json` is a draft written by Claude, not a customer-approved answer.
- Free-tier Gemini may use inputs to improve Google products: avoid confidential tenders on it.
- This is a drafting aid. An engineer must review every row before anything goes to a customer.
