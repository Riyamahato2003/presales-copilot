"""Read PDF / DOCX / XLSX into page-tagged chunks.

Design notes
- PDF text comes from PyMuPDF. Lines repeated on most pages (letterheads, footers, "Page : 2/10")
  are removed so they do not pollute retrieval.
- Chunks follow section headings, so a heading such as "NOT INCLUDED IN THE SCOPE OF SUPPLY"
  stays attached to its list, and the section carries over to the next page.
- Chunks whose section/text says "not included / excluded" are PINNED: the matcher always sees
  them, because supplier exclusion lists decide many compliance answers.
- `page` is the 1-based PDF page index, which can differ from a printed label like "8/10".
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .models import Chunk

MAX_CHARS = 1100
PIN_RE = re.compile(r"not included|excluded|exclusions?|out of scope|not in (?:the )?scope|deviations?", re.I)
PAGE_LABEL_RE = re.compile(r"^\s*page\s*:?\s*\d+\s*/\s*\d+\s*$", re.I)
HEADING_NUM_RE = re.compile(r"^\d+\)\s+\S")
FORM_RE = re.compile(r"technical\s+compliance|\(\s*yes\s*/\s*no\s*\)", re.I)


def is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 80:
        return False
    if HEADING_NUM_RE.match(s):
        return True
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 4 and s == s.upper() and not s.endswith(":")


def split_lines_to_chunks(lines: list[str], max_chars: int = MAX_CHARS) -> list[str]:
    """Greedy split on line boundaries; never cuts a line in half."""
    out, cur, size = [], [], 0
    for ln in lines:
        if cur and size + len(ln) + 1 > max_chars:
            out.append("\n".join(cur))
            cur, size = [], 0
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        out.append("\n".join(cur))
    return out


def _make_chunks(blocks: list[tuple[int, list[str]]], source: str, kind: str) -> list[Chunk]:
    """blocks: (page, lines). Walks lines, starting a new group at each heading."""
    chunks: list[Chunk] = []
    section = ""
    group: list[str] = []
    group_page = 1

    def flush() -> None:
        nonlocal group
        body = [g for g in group if g.strip()]
        if body:
            for piece in split_lines_to_chunks(body):
                cid = f"{Path(source).stem}-p{group_page}-{len(chunks)}"
                first_line = piece.split("\n", 1)[0]
                pinned = bool(PIN_RE.search(section) or PIN_RE.search(first_line))
                chunks.append(Chunk(id=cid, text=piece, source_file=Path(source).name,
                                    page=group_page, section=section, kind=kind, pinned=pinned,
                                    is_form=bool(FORM_RE.search(piece))))
        group = []

    for page, lines in blocks:
        if group and group_page != page:   # page break: flush but keep section for continuation
            flush()
        group_page = page
        for ln in lines:
            if is_heading(ln):
                if any(g.strip() and not is_heading(g) for g in group):
                    flush()
                    group_page = page
                section = ln.strip()
            group.append(ln)
    flush()
    return chunks


def ingest_pdf(path: str | Path) -> list[Chunk]:
    import pymupdf
    doc = pymupdf.open(str(path))
    pages = [[re.sub(r'[ \t]{2,}', ' ', l.strip()) for l in p.get_text(sort=True).splitlines()] for p in doc]
    # drop repeated letterhead/footer lines
    counts = Counter(l for pg in pages for l in set(pg) if l)
    threshold = max(3, int(0.6 * len(pages)))
    repeated = {l for l, c in counts.items() if c >= threshold}
    blocks = []
    for i, pg in enumerate(pages, start=1):
        keep = [l for l in pg if l and l not in repeated and not PAGE_LABEL_RE.match(l)]
        blocks.append((i, keep))
    return _make_chunks(blocks, str(path), "text")


def _docx_blocks(path: str | Path) -> list[str]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))

    def para_text(p: Paragraph) -> str:
        t = p.text.strip()
        if not t:
            return ""
        ppr = p._p.pPr
        return "- " + t if (ppr is not None and ppr.numPr is not None) else t

    lines: list[str] = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            t = para_text(Paragraph(child, doc))
            if t:
                lines.append(t)
        elif tag == "tbl":
            table = Table(child, doc)
            for row in table.rows:
                seen, cells = set(), []
                for cell in row.cells:
                    if id(cell._tc) in seen:        # merged cells repeat
                        continue
                    seen.add(id(cell._tc))
                    txt = "\n".join(x for x in (para_text(p) for p in cell.paragraphs) if x)
                    if txt:
                        cells.append(txt)
                if cells:
                    lines.append("ROW: " + " || ".join(cells))
    return lines


def ingest_docx(path: str | Path) -> list[Chunk]:
    raw = _docx_blocks(path)
    lines = [seg for block in raw for seg in block.split("\n")]
    return _make_chunks([(1, lines)], str(path), "docx")


def ingest_xlsx(path: str | Path) -> list[Chunk]:
    from openpyxl import load_workbook
    wb = load_workbook(str(path), read_only=True, data_only=True)
    chunks: list[Chunk] = []
    for idx, ws in enumerate(wb.worksheets, start=1):
        lines = [f"SHEET {ws.title}".upper()]
        for row in ws.iter_rows(values_only=True):
            vals = [str(v).strip() for v in row if v is not None and str(v).strip()]
            if vals:
                lines.append(" | ".join(vals))
        chunks += _make_chunks([(idx, lines)], str(path), "xlsx")
    return chunks


def ingest_file(path: str | Path) -> list[Chunk]:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return ingest_pdf(path)
    if ext == ".docx":
        return ingest_docx(path)
    if ext in (".xlsx", ".xlsm"):
        return ingest_xlsx(path)
    raise ValueError(f"Unsupported file type: {ext} ({path})")


def tender_text(path: str | Path) -> str:
    """Full tender text, in reading order, for requirement extraction."""
    return "\n".join(c.text for c in ingest_file(path))
