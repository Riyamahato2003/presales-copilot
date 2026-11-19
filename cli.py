#!/usr/bin/env python
"""Pre-Sales Copilot CLI.

  python cli.py parse data/brochures/X.pdf                  # see what was parsed, no API use
  python cli.py run --tender T.docx --brochure A.pdf --brochure B.pdf --out out/result.xlsx
  python cli.py run ... --dry-run                           # parse + estimate API calls, no API use
  python cli.py eval --results out/result.json --gold data/gold/x.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from copilot.ingest import ingest_file, tender_text


def cmd_parse(a) -> None:
    for c in ingest_file(a.file):
        print(f"--- {c.id} | page {c.page} | section={c.section!r} | pinned={c.pinned} | form={c.is_form}")
        print(c.text if a.full else c.text[:200])


def cmd_run(a) -> None:
    from copilot.llm import GeminiLLM
    from copilot.pipeline import run_pipeline

    if a.dry_run:
        text = tender_text(a.tender)
        n_chunks = {b: len(ingest_file(b)) for b in a.brochure}
        print(f"Tender text: {len(text)} chars. Brochure chunks: {n_chunks}")
        print("Estimated LLM calls: 1+ for requirement extraction, then 1 per requirement per brochure "
              "(e.g. 20 requirements x 2 brochures = ~41 calls). Embeddings: a few batched calls, or 0 with EMBED_BACKEND=local.")
        return
    llm = GeminiLLM()
    run_pipeline(llm, a.tender, a.brochure, a.out, top_k=a.top_k, model_name=llm.model)


def cmd_eval(a) -> None:
    from copilot.evaluate import evaluate, print_report
    print_report(evaluate(a.results, a.gold, a.product))


def main() -> None:
    ap = argparse.ArgumentParser(description="Pre-Sales Copilot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse"); p.add_argument("file"); p.add_argument("--full", action="store_true"); p.set_defaults(fn=cmd_parse)
    r = sub.add_parser("run")
    r.add_argument("--tender", required=True)
    r.add_argument("--brochure", action="append", required=True, help="repeat for several products")
    r.add_argument("--out", default="out/compliance_matrix.xlsx")
    r.add_argument("--top-k", type=int, default=6)
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(fn=cmd_run)
    e = sub.add_parser("eval"); e.add_argument("--results", required=True); e.add_argument("--gold", required=True)
    e.add_argument("--product"); e.set_defaults(fn=cmd_eval)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
