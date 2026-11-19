"""Compare tool output with a human-made gold file (data/gold/*.json)."""
from __future__ import annotations

import json
from collections import Counter
from difflib import SequenceMatcher

from .textutil import normalize, tokens


def _sim(gold: str, req_text: str) -> float:
    g, r = normalize(gold), normalize(req_text)
    ratio = SequenceMatcher(None, g, r, autojunk=False).ratio()
    gt, rt = set(tokens(gold)), set(tokens(req_text))
    contain = len(gt & rt) / len(gt) if gt else 0.0
    return max(ratio, contain)


def evaluate(results_json: str, gold_json: str, product: str | None = None, min_sim: float = 0.6) -> dict:
    res = json.loads(open(results_json, encoding="utf-8").read())
    gold = json.loads(open(gold_json, encoding="utf-8").read())
    product = product or next(iter(res["results"]))
    reqs = {r["req_id"]: r for r in res["requirements"]}
    status = {m["req_id"]: m["status"] for m in res["results"][product]}

    rows, confusion = [], Counter()
    for g in gold["rows"]:
        best_id, best = None, 0.0
        for rid, r in reqs.items():
            s = _sim(g["clause"], r["clause_text"] + " " + r["parameter"])
            if s > best:
                best_id, best = rid, s
        if best_id is None or best < min_sim:
            rows.append({"clause": g["clause"], "gold": g["status"], "tool": None, "match": round(best, 2), "ok": False,
                         "unsafe": False, "note": "no matching requirement extracted"})
            continue
        tool = status[best_id]
        ok = tool == g["status"]
        unsafe = tool == "Compliant" and g["status"] != "Compliant"
        confusion[(g["status"], tool)] += 1
        rows.append({"clause": g["clause"], "gold": g["status"], "tool": tool, "req_id": best_id,
                     "match": round(best, 2), "ok": ok, "unsafe": unsafe, "note": g.get("note", "")})
    n = len(rows)
    return {
        "product": product, "gold_rows": n,
        "exact_match": sum(r["ok"] for r in rows), "accuracy": round(sum(r["ok"] for r in rows) / n, 3) if n else 0,
        "unsafe_compliant": sum(r["unsafe"] for r in rows),
        "unmatched": sum(r["tool"] is None for r in rows),
        "rows": rows, "confusion": {f"{a} -> {b}": c for (a, b), c in sorted(confusion.items())},
    }


def print_report(rep: dict) -> None:
    print(f"\nProduct: {rep['product']}   gold rows: {rep['gold_rows']}")
    print(f"Exact status match: {rep['exact_match']}/{rep['gold_rows']} ({rep['accuracy']:.0%})")
    print(f"UNSAFE (tool says Compliant, gold says otherwise): {rep['unsafe_compliant']}   "
          f"gold rows with no extracted requirement: {rep['unmatched']}\n")
    for r in rep["rows"]:
        mark = "OK " if r["ok"] else ("!! " if r["unsafe"] else "-- ")
        print(f"{mark}gold={r['gold']:<18} tool={str(r['tool']):<18} | {r['clause'][:70]}")
