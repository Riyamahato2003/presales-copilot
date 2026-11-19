import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from copilot.evaluate import evaluate
from copilot.ingest import ingest_file, split_lines_to_chunks, tender_text
from copilot.llm import FakeLLM
from copilot.models import Chunk, RawMatch, Requirement
from copilot.pipeline import run_pipeline
from copilot.requirements_extract import SYSTEM as REQ_SYSTEM
from copilot.verify import quote_in_text, verify

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "data/brochures/BCSA_SP160_offer.pdf"
DOCX = ROOT / "data/tenders/axial_compressor_gearbox_tender.docx"


# ---------- ingestion ----------
def test_split_never_cuts_lines():
    lines = [f"line {i} " + "x" * 50 for i in range(40)]
    for piece in split_lines_to_chunks(lines, 300):
        assert all(l in lines for l in piece.split("\n"))


def test_pdf_chunks_pages_pins_and_letterhead_removed():
    chunks = ingest_file(PDF)
    assert {c.page for c in chunks} >= {1, 2, 5, 6, 7}
    pinned = [c for c in chunks if c.pinned]
    assert pinned and any("Oil for first filling" in c.text for c in pinned)
    assert any("Foundation frame" in c.text for c in pinned)         # continuation on next page keeps the pin
    assert not any("Specialist in Power Transmission" in c.text for c in chunks)
    assert any("Maximum torque (HS)" in c.text and "135" in c.text for c in chunks)
    assert any(c.is_form for c in chunks)                              # Appendix 1 echo is flagged


def test_docx_tender_text_has_all_clauses():
    t = tender_text(DOCX)
    for needle in ("Minimum service factor of 1.5 as per AGMA 6011", "shaft driven pump", "Minimum of 12 months"):
        assert needle in t


# ---------- verification ----------
def _req(text="Output speed range: 760 - 6500 rpm"):
    return Requirement(req_id="R-001", clause_ref="Item 1", clause_text=text, parameter="Output speed",
                       required_value="760-6500 rpm", category="technical", priority="mandatory")


CH = Chunk(id="c1", text="Maximum output speed (HS) rpm : 6 500\nMaximum torque (HS) N.m : 135", source_file="x.pdf", page=2)


def _raw(**kw):
    base = dict(status="Compliant", offered_value="6500 rpm", evidence_chunk_id="c1",
                evidence_quote="Maximum output speed (HS) rpm : 6 500", reasoning="r", confidence=0.9)
    base.update(kw)
    return RawMatch(**base)


def test_quote_matching_is_tolerant_to_thousand_separators():
    assert quote_in_text("rpm : 6500", CH.text)
    assert not quote_in_text("rpm : 7500 and more", CH.text)


def test_verify_keeps_good_answer_and_sets_page():
    r = verify(_raw(), _req(), {"c1": CH}, "p")
    assert r.status == "Compliant" and r.page == 2 and r.source_file == "x.pdf"


@pytest.mark.parametrize("kw,expect", [
    (dict(evidence_quote="Maximum output speed is 9000 rpm"), "not found in the cited"),
    (dict(evidence_chunk_id="nope"), "not among"),
    (dict(offered_value="9000 rpm"), "numbers not in"),
    (dict(confidence=0.4), "low confidence"),
    (dict(evidence_quote="Output speed range: 760 - 6500 rpm"), "not found"),
])
def test_verify_downgrades_bad_evidence(kw, expect):
    r = verify(_raw(**kw), _req(), {"c1": CH}, "p")
    assert r.status == "Needs human review" and expect in r.verifier_note


def test_verify_downgrades_form_echo():
    form = CH.model_copy(update={"is_form": True})
    assert verify(_raw(), _req(), {"c1": form}, "p").status == "Needs human review"


def test_not_found_is_left_alone():
    r = verify(_raw(status="Not found", offered_value="", evidence_chunk_id="", evidence_quote=""), _req(), {}, "p")
    assert r.status == "Not found"


# ---------- end to end with a fake LLM (plumbing only, no Gemini) ----------
def _fake_responder(prompt: str, system: str):
    if system == REQ_SYSTEM:
        mk = lambda ref, text, par, val, pr="mandatory": dict(clause_ref=ref, clause_text=text, parameter=par,
                                                           required_value=val, category="technical", priority=pr)
        return [
            mk("Item 1", "Overload torque capability: 150% of rated torque for short-duration transient conditions", "Overload torque", "150%"),
            mk("Item 1", "Minimum service factor of 1.5 as per AGMA 6011", "Service factor", "1.5 AGMA 6011"),
            mk("Item 2 - Warranty", "Minimum of 12 months from the date of commissioning", "Warranty", "12 months", "mandatory"),
        ]
    nf = dict(status="Not found", offered_value="", evidence_chunk_id="", evidence_quote="", reasoning="n/a", confidence=0.9)
    param = next(l for l in prompt.splitlines() if l.startswith("Parameter:"))
    if "Overload torque" in param:
        cid = next(l[1:l.index("]")] for l in prompt.splitlines() if l.startswith("[") and "203" in prompt.split(l, 1)[1].split("\n[")[0])
        return dict(status="Compliant", offered_value="203 N.m", evidence_chunk_id=cid,
                    evidence_quote="Maximum transient overtorque N.m : 203 (150%)", reasoning="stated", confidence=0.9)
    if "Warranty" in param:   # fabricated evidence -> must be downgraded
        return dict(status="Compliant", offered_value="24 months", evidence_chunk_id="made-up",
                    evidence_quote="Warranty 24 months", reasoning="invented", confidence=0.95)
    return nf


def test_pipeline_end_to_end(tmp_path):
    out = tmp_path / "m.xlsx"
    payload = run_pipeline(FakeLLM(_fake_responder), str(DOCX), [str(PDF)], str(out), model_name="fake", log=lambda *_: None)
    res = {m["req_id"]: m for m in payload["results"]["BCSA_SP160_offer"]}
    assert res["R-001"]["status"] == "Compliant" and res["R-001"]["page"] == 2
    assert res["R-002"]["status"] == "Not found"
    assert res["R-003"]["status"] == "Needs human review"              # fabricated evidence caught
    wb = load_workbook(out)
    assert wb.sheetnames[0] == "Summary" and wb.sheetnames[1].startswith("Matrix - ")
    ws = wb[wb.sheetnames[1]]
    assert ws["F2"].value == "Compliant" and ws["N2"].value.startswith("=IF(")
    assert Path(out).with_suffix(".json").exists()
    gold = ROOT / "data/gold/axial_compressor_gold.json"
    rep = evaluate(str(Path(out).with_suffix(".json")), str(gold))
    assert rep["gold_rows"] == len(json.loads(gold.read_text())["rows"]) and rep["unsafe_compliant"] == 0
