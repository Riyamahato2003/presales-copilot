from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .export import write_workbook
from .ingest import ingest_file, tender_text
from .match import match_requirement
from .models import MatchResult, Requirement
from .llm import QuotaExceeded
from .requirements_extract import extract_requirements
from .retrieve import Index
from .source_layout import link_requirements


def run_pipeline(llm, tender: str, brochures: list[str], out_xlsx: str, top_k: int = 6,
                 model_name: str = "", log=print) -> dict:
    log(f"Reading tender: {tender}")
    tender_chunks = ingest_file(tender)
    if not tender_chunks:
        raise ValueError("No readable tender content.")
    tender_ocr = any(c.kind == "ocr" for c in tender_chunks)
    reqs: list[Requirement] = extract_requirements(llm, "\n".join(c.text for c in tender_chunks if c.kind != "pdf_table"))
    link_requirements(reqs, tender_chunks)
    if tender_ocr:
        for r in reqs:
            r.extraction_note = (r.extraction_note + " OCR used in tender; verify requirement against source page.").strip()
    log(f"  extracted {len(reqs)} requirements")

    results: dict[str, list[MatchResult]] = {}
    quota_stopped = False
    for b in brochures:
        product = Path(b).stem
        chunks = ingest_file(b)
        log(f"Brochure {Path(b).name}: {len(chunks)} chunks, {sum(c.pinned for c in chunks)} pinned")
        rows = []
        if quota_stopped:
            rows = [MatchResult(status="Needs human review", offered_value="", evidence_chunk_id="",
                evidence_quote="", reasoning="No compliance assessment was run after provider quota exhaustion.",
                confidence=0.0, req_id=r.req_id, product=product, verifier_note="Not run: provider quota exhausted.",
                extraction_note=r.extraction_note, run_state="not_run") for r in reqs]
            results[product] = rows
            continue
        try:
            index = Index(chunks, llm, product)
        except QuotaExceeded as exc:
            log(f"  Provider quota stopped retrieval: {exc}")
            quota_stopped = True
            index = None
        for i, r in enumerate(reqs, 1):
            if quota_stopped:
                rows.append(MatchResult(status="Needs human review", offered_value="", evidence_chunk_id="",
                    evidence_quote="", reasoning="No compliance assessment was run after provider quota exhaustion.",
                    confidence=0.0, req_id=r.req_id, product=product, verifier_note="Not run: provider quota exhausted.",
                    extraction_note=r.extraction_note, run_state="not_run"))
                continue
            log(f"  [{product}] {i}/{len(reqs)} {r.req_id} {r.parameter[:40]}")
            try:
                match = match_requirement(llm, r, index, k=top_k)
            except QuotaExceeded as exc:
                log(f"  Provider quota exhausted at {r.req_id}; remaining rows marked Not run.")
                quota_stopped = True
                rows.append(MatchResult(status="Needs human review", offered_value="", evidence_chunk_id="",
                    evidence_quote="", reasoning="No compliance assessment was produced because provider quota was exhausted.",
                    confidence=0.0, req_id=r.req_id, product=product,
                    verifier_note=f"API error: {exc}", extraction_note=r.extraction_note, run_state="api_error"))
                continue
            match.extraction_note = r.extraction_note
            if r.extraction_note and match.status in {"Compliant", "Partial", "Non-compliant"}:
                match.verifier_note = (match.verifier_note + f" Downgraded from {match.status}: tender extraction requires review.").strip()
                match.status = "Needs human review"
            rows.append(match)
        results[product] = rows

    meta = {"tender": Path(tender).name, "model": model_name, "run_at": datetime.now().isoformat(timespec="seconds")}
    write_workbook(out_xlsx, reqs, results, meta)
    payload = {"meta": meta, "requirements": [r.model_dump() for r in reqs],
               "results": {p: [m.model_dump() for m in ms] for p, ms in results.items()}}
    Path(out_xlsx).with_suffix(".json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Wrote {out_xlsx} and {Path(out_xlsx).with_suffix('.json')}")
    return payload
