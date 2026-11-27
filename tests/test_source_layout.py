"""Source provenance tests: synthetic fixtures only."""
import pymupdf
from openpyxl import load_workbook
from copilot.ingest import ingest_file, tender_text
from copilot.models import Chunk, Requirement, RawMatch
from copilot.source_layout import link_requirements
from copilot.verify import verify
from copilot.export import write_workbook


def requirement(text="Minimum travel 25 mm"):
    return Requirement(req_id="R-001", clause_ref="1", clause_text=text,
                       parameter="Travel", required_value="25 mm", category="technical", priority="mandatory")


def test_native_ruled_table_retains_cells_and_regions(tmp_path):
    path = tmp_path / "table.pdf"
    with pymupdf.open() as d:
        p = d.new_page(width=400, height=300)
        for x in (40, 170, 360):
            p.draw_line((x, 50), (x, 130))
        for y in (50, 90, 130):
            p.draw_line((40, y), (360, y))
        for x,y,t in [(48,73,"Parameter"),(178,73,"Specification"),(48,113,"Travel"),(178,113,"Minimum travel 25 mm")]:
            p.insert_text((x,y), t, fontsize=11)
        d.save(path)
    chunks = ingest_file(path)
    rows = [c for c in chunks if c.kind == "pdf_table"]
    assert any("Travel || Minimum travel 25 mm" in c.text and c.regions for c in rows)
    assert any(c.kind == "text" for c in chunks)
    assert "Minimum travel 25 mm" in tender_text(path)
    assert "ROW:" not in tender_text(path)
    req = requirement()
    link_requirements([req], chunks)
    assert req.source_page == 1
    assert req.extraction_note  # detected table evidence remains conservative


def test_native_region_and_ambiguous_requirement(tmp_path):
    path = tmp_path / "native.pdf"
    with pymupdf.open() as d:
        d.new_page().insert_text((72,100),"Minimum travel 25 mm")
        d.new_page().insert_text((72,100),"Minimum travel 25 mm")
        d.save(path)
    chunks = ingest_file(path)
    assert chunks[0].regions and chunks[0].regions[0][0] >= 70
    req = requirement()
    link_requirements([req], chunks)
    assert req.source_page is None
    assert "multiple source" in req.extraction_note


def test_verified_chunk_region_and_excel_literal(tmp_path):
    req = requirement()
    chunk = Chunk(id="c", text="XY travel: 25 mm", source_file="offer.pdf", page=2,
                  locator="PDF page 2, text region", regions=[(10,20,50,40)])
    raw = RawMatch(status="Compliant", offered_value="25 mm", evidence_chunk_id="c",
                   evidence_quote="XY travel: 25 mm", reasoning="=1+1", confidence=.9)
    match = verify(raw, req, {"c":chunk}, "sample")
    assert match.evidence_regions == [(10,20,50,40)]
    req.source_file = "tender.pdf"
    req.source_locator = "PDF page 1"
    path = tmp_path / "out.xlsx"
    write_workbook(str(path), [req], {"sample":[match]}, {})
    sheet = load_workbook(path)["Matrix - sample"]
    assert sheet["J2"].data_type == "s" and sheet["J2"].value == "=1+1"
    assert sheet["S2"].value == "tender.pdf, PDF page 1"
    assert sheet["T2"].value == "text"
    assert sheet["P2"].data_type == "f"
