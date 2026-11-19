from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .export import write_workbook
from .ingest import ingest_file, tender_text
from .match import match_requirement
from .models import MatchResult, Requirement
from .requirements_extract import extract_requirements
from .retrieve import Index


def run_pipeline(llm, tender: str, brochures: list[str], out_xlsx: str, top_k: int = 6,
                 model_name: str = "", log=print) -> dict:
    log(f"Reading tender: {tender}")
    reqs: list[Requirement] = extract_requirements(llm, tender_text(tender))
    log(f"  extracted {len(reqs)} requirements")

    results: dict[str, list[MatchResult]] = {}
    for b in brochures:
        product = Path(b).stem
        chunks = ingest_file(b)
        log(f"Brochure {Path(b).name}: {len(chunks)} chunks, {sum(c.pinned for c in chunks)} pinned")
        index = Index(chunks, llm, product)
        rows = []
        for i, r in enumerate(reqs, 1):
            log(f"  [{product}] {i}/{len(reqs)} {r.req_id} {r.parameter[:40]}")
            rows.append(match_requirement(llm, r, index, k=top_k))
        results[product] = rows

    meta = {"tender": Path(tender).name, "model": model_name, "run_at": datetime.now().isoformat(timespec="seconds")}
    write_workbook(out_xlsx, reqs, results, meta)
    payload = {"meta": meta, "requirements": [r.model_dump() for r in reqs],
               "results": {p: [m.model_dump() for m in ms] for p, ms in results.items()}}
    Path(out_xlsx).with_suffix(".json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Wrote {out_xlsx} and {Path(out_xlsx).with_suffix('.json')}")
    return payload
