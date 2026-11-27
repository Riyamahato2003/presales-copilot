"""Synthetic regression cases only; no customer documents or model calls."""
import shutil
import subprocess
import pytest
import pymupdf

from copilot.document_io import page_needs_ocr, legacy_word_text
from copilot.ingest import ingest_file
from copilot.models import Chunk, RawMatch, Requirement
from copilot.verify import verify
from webapp import valid_file


def make_scan(path, hidden=False):
    with pymupdf.open() as source:
        page = source.new_page(width=612, height=792)
        page.insert_text((72, 100), "Sample instrument warranty 24 months.", fontsize=20)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        with pymupdf.open() as target:
            p = target.new_page(width=612, height=792)
            p.insert_image(p.rect, pixmap=pix)
            if hidden:
                p.insert_text((72, 100), "Corrupted invisible layer", render_mode=3)
            target.save(path)


@pytest.mark.parametrize("hidden", [False, True])
def test_scan_routes_to_local_ocr(tmp_path, hidden):
    path = tmp_path / "scan.pdf"
    make_scan(path, hidden)
    with pymupdf.open(path) as doc:
        assert page_needs_ocr(doc[0])
    if not shutil.which("tesseract"):
        pytest.skip("Tesseract not installed")
    chunks = ingest_file(path)
    assert chunks and all(c.kind == "ocr" and c.page == 1 for c in chunks)
    text = " ".join(c.text for c in chunks).lower()
    assert "warranty" in text and "24" in text
    assert "corrupted invisible layer" not in text


def test_visible_native_text_avoids_ocr(tmp_path):
    path = tmp_path / "native.pdf"
    with pymupdf.open() as d:
        d.new_page().insert_text((72, 100), "Sample warranty 24 months.")
        d.save(path)
    with pymupdf.open(path) as d:
        assert not page_needs_ocr(d[0])
    assert ingest_file(path)[0].kind == "text"


def test_ocr_failure_does_not_silently_drop_page(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    make_scan(path)
    def fail(page):
        raise ValueError("OCR unavailable")
    monkeypatch.setattr("copilot.ingest.local_ocr", fail)
    with pytest.raises(ValueError, match="Page 1: OCR unavailable"):
        ingest_file(path)


def test_ocr_evidence_cannot_auto_pass():
    chunk = Chunk(id="c1", text="Warranty 24 months.", source_file="scan.pdf", page=2, kind="ocr")
    req = Requirement(req_id="r1", clause_ref="1", clause_text="At least 24 months warranty", parameter="Warranty",
                      required_value="24 months", category="warranty", priority="mandatory")
    raw = RawMatch(status="Compliant", offered_value="24 months", evidence_chunk_id="c1",
                   evidence_quote="Warranty 24 months.", reasoning="stated", confidence=0.99)
    result = verify(raw, req, {"c1": chunk}, "sample")
    assert result.status == "Needs human review"
    assert result.page == 2 and "OCR-derived" in result.verifier_note


def test_legacy_doc_signature_and_reader(tmp_path, monkeypatch):
    path = tmp_path / "sample.doc"
    path.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"test fixture")
    assert valid_file(path.name, path.read_bytes())
    assert not valid_file("wrong.doc", b"wrong")
    monkeypatch.setattr("copilot.document_io.subprocess.run",
                        lambda *a, **kw: subprocess.CompletedProcess(a, 0, b"Sample warranty 24 months.", b""))
    chunks = ingest_file(path)
    assert chunks[0].kind == "legacy_doc"
    assert "24 months" in chunks[0].text
    wrong = tmp_path / "wrong.doc"
    wrong.write_bytes(b"wrong")
    with pytest.raises(ValueError):
        legacy_word_text(wrong)
