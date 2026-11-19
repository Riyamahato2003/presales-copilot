import json
import re
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from docx import Document
from openpyxl import load_workbook

from copilot.ingest import ingest_file, split_lines_to_chunks, tender_text
from copilot.llm import Cache, FakeLLM, GeminiLLM, QuotaExceeded
from copilot.models import Chunk, RawMatch, Requirement
from copilot.pipeline import run_pipeline
from copilot.requirements_extract import SYSTEM as REQ_SYSTEM, extract_requirements
from copilot.verify import quote_in_text, verify
from fastapi.testclient import TestClient
from webapp import app, valid_file


@pytest.fixture(scope="session")
def docs(tmp_path_factory):
    folder = tmp_path_factory.mktemp("synthetic_docs")
    pdf = folder / "sample_offer.pdf"
    doc = fitz.open()
    for i in range(7):
        p = doc.new_page()
        p.insert_text((40, 40), "Specialist in Power Transmission")
        if i == 1:
            p.insert_text((40, 100), "GEAR DATA\nMaximum output speed (HS) rpm : 6 500\nMaximum torque (HS) N.m : 135\nMaximum transient overtorque N.m : 203 (150%)")
        if i == 4:
            p.insert_text((40, 100), "NOT INCLUDED IN THE SCOPE OF SUPPLY\n- Oil for first filling\n- Foundation frame")
        if i == 5:
            p.insert_text((40, 100), "Foundation frame supplied by customer")
    doc.save(pdf)
    doc.close()

    tender = folder / "sample_tender.docx"
    d = Document()
    d.add_paragraph("Axial compressor gearbox requirements")
    table = d.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Item 1 - Gearbox"
    table.cell(0, 1).text = "Overload torque capability: 150% of rated torque for short-duration transient conditions\nMinimum service factor of 1.5 as per AGMA 6011"
    d.add_paragraph("Item 2 - Warranty")
    d.add_paragraph("Minimum of 12 months from the date of commissioning")
    d.save(tender)
    gold = folder / "gold.json"
    gold.write_text(json.dumps({"rows": [{"clause": "Minimum service factor of 1.5 as per AGMA 6011", "status": "Not found"}]}))
    return pdf, tender, gold


def test_split_never_cuts_lines():
    lines = [f"line {i} " + "x" * 50 for i in range(40)]
    for piece in split_lines_to_chunks(lines, 300):
        assert all(l in lines for l in piece.split("\n"))


def test_synthetic_pdf_chunks_pages_pins_and_repeated_header_removed(docs):
    pdf, _, _ = docs
    chunks = ingest_file(pdf)
    assert {c.page for c in chunks} >= {2, 5, 6}
    pinned = [c for c in chunks if c.pinned]
    assert pinned and any("Oil for first filling" in c.text for c in pinned)
    assert any("Foundation frame" in c.text for c in pinned)
    assert not any("Specialist in Power Transmission" in c.text for c in chunks)
    assert any("Maximum torque (HS)" in c.text and "135" in c.text for c in chunks)


def test_docx_tender_text_has_clauses(docs):
    _, tender, _ = docs
    text = tender_text(tender)
    for clause in ("Minimum service factor of 1.5 as per AGMA 6011", "Minimum of 12 months"):
        assert clause in text


def test_requirement_grounding_tolerates_table_markers_and_preserves_duplicate_sections():
    class RequirementsLLM:
        def generate_json(self, *_):
            return [dict(clause_ref="Item 1", clause_text="Speed range 350 - 2980 rpm", parameter="Speed", required_value="350-2980 rpm", category="technical", priority="mandatory"),
                    dict(clause_ref="Item 2", clause_text="Speed range 350 - 2980 rpm", parameter="Speed", required_value="350-2980 rpm", category="technical", priority="mandatory")]
    tender="ROW: Item 1 || Speed range 350 - 2980 rpm\nROW: Item 2 || Speed range 350 - 2980 rpm"
    reqs=extract_requirements(RequirementsLLM(),tender)
    assert len(reqs)==2
    assert all(not r.extraction_note for r in reqs)


def test_requirement_grounding_flags_changed_number():
    class RequirementsLLM:
        def generate_json(self, *_):
            return [dict(clause_ref="Item 1", clause_text="Minimum speed is 210 rpm", parameter="Speed", required_value="210 rpm", category="technical", priority="mandatory")]
    reqs=extract_requirements(RequirementsLLM(),"ROW: Item 1 || Minimum speed is 120 rpm")
    assert "numbers" in reqs[0].extraction_note


def req(text="The operating envelope must cover the specified speeds", required="760-6500 rpm"):
    return Requirement(req_id="R-001", clause_ref="Item 1", clause_text=text, parameter="Output speed",
                       required_value=required, category="technical", priority="mandatory")


CH = Chunk(id="c1", text="Output speed range: 760 - 6 500 rpm\nMaximum output speed rpm : 6 500\nMaximum torque (HS) N.m : 135", source_file="x.pdf", page=2)


def raw(**kw):
    base = dict(status="Compliant", offered_value="760-6500 rpm", evidence_chunk_id="c1",
                evidence_quote="Output speed range: 760 - 6 500 rpm", reasoning="stated", confidence=0.9)
    base.update(kw)
    return RawMatch(**base)


def test_quote_matching_is_tolerant_to_thousand_separators_and_rejects_digit_changes():
    assert quote_in_text("speed range: 760-6500 rpm", CH.text)
    assert not quote_in_text("speed range: 760-6800 rpm", CH.text)


def test_verify_keeps_grounded_answer_and_sets_page():
    result = verify(raw(), req(), {"c1": CH}, "p")
    assert result.status == "Compliant" and result.page == 2 and result.source_file == "x.pdf"


@pytest.mark.parametrize("kwargs,expected", [
    ({"evidence_quote": "Output speed range: 760 - 6800 rpm"}, "not found in the cited"),
    ({"evidence_chunk_id": "nope"}, "not among"),
    ({"offered_value": "6800 rpm"}, "numbers not in"),
    ({"confidence": 0.4}, "low confidence"),
    ({"evidence_quote": "Maximum output speed rpm : 6 500", "offered_value": "6500 rpm"}, "all required numbers"),
])
def test_verify_downgrades_unsafe_evidence(kwargs, expected):
    result = verify(raw(**kwargs), req(), {"c1": CH}, "p")
    assert result.status == "Needs human review" and expected in result.verifier_note


def test_quote_number_must_exist_in_chunk():
    assert not quote_in_text("torque 999 N.m", CH.text)


def test_verify_downgrades_supplier_form_echo():
    form = CH.model_copy(update={"is_form": True})
    assert verify(raw(), req(), {"c1": form}, "p").status == "Needs human review"


def fake_responder(prompt, system):
    if system == REQ_SYSTEM:
        return [dict(clause_ref="Item 1", clause_text="Overload torque capability: 150% of rated torque for short-duration transient conditions", parameter="Overload torque", required_value="150%", category="technical", priority="mandatory"),
                dict(clause_ref="Item 1", clause_text="Minimum service factor of 1.5 as per AGMA 6011", parameter="Service factor", required_value="1.5 AGMA 6011", category="technical", priority="mandatory"),
                dict(clause_ref="Item 2 - Warranty", clause_text="Minimum of 12 months from the date of commissioning", parameter="Warranty", required_value="12 months", category="warranty", priority="mandatory")]
    cid_match = re.search(r"\[([^\]]+)\].*?<supplier_excerpt>.*?Maximum transient.*?</supplier_excerpt>", prompt, re.S)
    cid = cid_match.group(1) if cid_match else ""
    if "Parameter: Overload torque" in prompt:
        return dict(status="Compliant", offered_value="203 N.m (150%)", evidence_chunk_id=cid,
                    evidence_quote="Maximum transient overtorque N.m : 203 (150%)", reasoning="explicit", confidence=.9)
    return dict(status="Not found", offered_value="", evidence_chunk_id="", evidence_quote="", reasoning="No supplier evidence in excerpts.", confidence=.9)


def test_pipeline_end_to_end_and_export(docs, tmp_path):
    pdf, tender, _ = docs
    output = tmp_path / "matrix.xlsx"
    payload = run_pipeline(FakeLLM(fake_responder), str(tender), [str(pdf)], str(output), model_name="fake", log=lambda *_: None)
    results = {m["req_id"]: m for m in payload["results"]["sample_offer"]}
    assert results["R-001"]["status"] == "Compliant" and results["R-001"]["page"] == 2
    wb = load_workbook(output)
    ws = wb["Matrix - sample_offer"]
    assert ws["F2"].value == "Compliant" and ws["L2"].value == "completed"
    assert ws["P2"].value.startswith("=IF(")
    assert "COUNTIFS" in wb["Summary"]["B6"].value


def test_quota_error_stops_remaining_rows_and_marks_run_state(docs, tmp_path):
    pdf, tender, _ = docs
    class QuotaLLM(FakeLLM):
        def generate_json(self, prompt, system, schema):
            if system == REQ_SYSTEM:
                return fake_responder(prompt, system)
            raise QuotaExceeded("daily quota exhausted")
    out = tmp_path / "partial.xlsx"
    payload = run_pipeline(QuotaLLM(lambda *_: []), str(tender), [str(pdf)], str(out), log=lambda *_: None)
    rows = payload["results"]["sample_offer"]
    assert rows[0]["run_state"] == "api_error"
    assert all(r["run_state"] == "not_run" for r in rows[1:])
    assert out.exists()


def test_quota_retry_is_bounded_and_daily_quota_stops_immediately(monkeypatch):
    client=GeminiLLM.__new__(GeminiLLM)
    client.min_interval=0; client._last_call=0
    sleeps=[]; monkeypatch.setattr('copilot.llm.time.sleep',lambda n:sleeps.append(n))
    class ProviderError(Exception):
        code=429
    calls=[]
    def daily():
        calls.append(1); raise ProviderError('GenerateRequestsPerDayPerModel quota exceeded')
    with pytest.raises(QuotaExceeded): client._with_backoff(daily)
    assert len(calls)==1 and not sleeps


def test_invalid_structured_output_is_not_cached(tmp_path):
    client=GeminiLLM.__new__(GeminiLLM)
    responses=[SimpleNamespace(text='{"status":"not-a-valid-status"}'),
               SimpleNamespace(text=json.dumps(dict(status="Not found",offered_value="",evidence_chunk_id="",evidence_quote="",reasoning="No evidence",confidence=.9)))]
    class Models:
        calls=0
        def generate_content(self,**kwargs):
            self.calls+=1
            return responses.pop(0)
    models=Models()
    client.model='test-model'; client.cache=Cache(tmp_path/'cache.sqlite'); client.types=SimpleNamespace(GenerateContentConfig=lambda **kw:kw)
    client.client=SimpleNamespace(models=models); client.min_interval=0; client._last_call=0
    client._throttle=lambda:None
    with pytest.raises(Exception): client.generate_json('same prompt','system',RawMatch)
    answer=client.generate_json('same prompt','system',RawMatch)
    assert answer['status']=='Not found' and models.calls==2


def test_public_deployment_requires_password_and_upload_signatures(monkeypatch):
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.delenv('COPILOT_ACCESS_PASSWORD',raising=False)
    c=TestClient(app)
    assert c.get('/').status_code==503
    monkeypatch.setenv('COPILOT_ACCESS_PASSWORD','test-only-password')
    assert c.get('/').status_code==401
    assert c.get('/',auth=('copilot','test-only-password')).status_code==200
    assert valid_file('offer.pdf',b'%PDF-1.7 sample')
    assert not valid_file('offer.pdf',b'not really a pdf')
    assert not valid_file('offer.docx',b'PK not a valid docx')
