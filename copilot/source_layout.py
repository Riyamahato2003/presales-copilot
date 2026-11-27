"""Conservative source-region index for native PDF text and ruled tables.

Regions identify whole evidence chunks or rows, not exact quote coordinates.
Never replace the primary extracted text with heuristic table detection.
"""
from __future__ import annotations

from collections import defaultdict
import logging
import math
from .models import Chunk, Requirement
from .textutil import normalize

log = logging.getLogger(__name__)
MAX_TABLE_ROWS = 250
MAX_DRAWINGS = 3000


def clean(text: str) -> str:
    return normalize(text.replace("ROW:", " ").replace("||", " "))


def box(rect):
    if rect is None:
        return None
    coords = tuple(float(x) for x in rect)
    if len(coords) != 4 or not all(math.isfinite(x) for x in coords):
        return None
    if coords[2] <= coords[0] or coords[3] <= coords[1]:
        return None
    return coords


def native_regions(page, chunks: list[Chunk]) -> list[Chunk]:
    """Exact normalized line lookup; repeated or unmatched lines get page-only references."""
    if page.rotation:
        return chunks
    by_line = defaultdict(list)
    for block in page.get_text("dict", flags=0).get("blocks", []):
        for line in block.get("lines", []):
            key = clean("".join(span["text"] for span in line.get("spans", [])))
            region = box(line.get("bbox"))
            if key and region:
                by_line[key].append(region)
    out = []
    for chunk in chunks:
        keys = [clean(line) for line in chunk.text.splitlines() if clean(line)]
        regions = [by_line[k][0] for k in keys if len(by_line[k]) == 1]
        if keys and len(regions) == len(keys):
            chunk = chunk.model_copy(update={"regions": list(dict.fromkeys(regions)), "locator": f"PDF page {chunk.page}, text region"})
        out.append(chunk)
    return out


def native_table_rows(page, source: str, page_number: int) -> list[Chunk]:
    """Supplemental rows from native ruled tables. Preserve blanks and cell order."""
    if page.rotation:
        return []
    from .ingest import PIN_RE, FORM_RE
    try:
        drawings = page.get_drawings()
        if len(drawings) > MAX_DRAWINGS:
            log.warning("Table detection skipped: drawing limit")
            return []
        tables = page.find_tables(paths=drawings).tables
    except (AttributeError, TypeError, ValueError, RuntimeError):
        log.warning("Native table detection unavailable; primary text retained")
        return []
    rows = []
    for t_idx, table in enumerate(tables, 1):
        if table.col_count < 2 or table.row_count < 2:
            continue
        try:
            extracted = table.extract()
            form = any(FORM_RE.search(str(cell or "")) for row in extracted for cell in row)
            for r_idx, (cells, geometry) in enumerate(zip(extracted, table.rows), 1):
                if len(rows) >= MAX_TABLE_ROWS:
                    log.warning("Table row limit reached; primary text retained")
                    return rows
                values = [" ".join(str(c or "").split()) for c in cells]
                if not any(values):
                    continue
                region = box(geometry.bbox)
                if not region:
                    continue
                text = "ROW: " + " || ".join(values)
                rows.append(Chunk(id=f"table-p{page_number}-t{t_idx}-r{r_idx}", text=text,
                                  source_file=source, page=page_number, kind="pdf_table",
                                  section=f"Detected table {t_idx}", pinned=bool(PIN_RE.search(text)),
                                  is_form=form, locator=f"PDF page {page_number}, table {t_idx}, row {r_idx}",
                                  regions=[region]))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            log.warning("Table row extraction incomplete; primary text retained")
    return rows


def link_requirements(requirements: list[Requirement], chunks: list[Chunk]) -> None:
    """Attach only exact, unambiguous source references; no fuzzy locator guesses."""
    for req in requirements:
        needle = clean(req.clause_text)
        matches = [c for c in chunks if needle and needle in clean(c.text)]
        if not matches:
            req.extraction_note = (req.extraction_note + " No exact source chunk located; review original document.").strip()
            continue
        pages = {(c.source_file, c.page) for c in matches}
        if len(pages) != 1:
            req.extraction_note = (req.extraction_note + " Requirement occurs in multiple source locations; review manually.").strip()
            continue
        c = matches[0]
        req.source_file = c.source_file
        req.source_page = c.page if c.kind in {"text", "ocr", "pdf_table"} else None
        if len(matches) == 1:
            req.source_locator = c.locator
            req.source_regions = c.regions
        elif req.source_page is not None:
            req.source_locator = f"PDF page {req.source_page} (multiple candidate regions)"
        if any(m.kind in {"ocr", "pdf_table", "legacy_doc"} for m in matches):
            req.extraction_note = (req.extraction_note + " OCR, detected table or legacy Word evidence requires source review.").strip()
