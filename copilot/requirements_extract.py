"""Turn a tender document into a list of atomic, verbatim requirements."""
from __future__ import annotations

from difflib import SequenceMatcher

from .ingest import split_lines_to_chunks
from .models import RawRequirement, Requirement
from .textutil import normalize

SYSTEM = """You extract technical and commercial requirements from tender / RFQ documents for a
pre-sales engineer. Output a JSON list; each item is ONE atomic requirement.

Rules:
- clause_text must be copied VERBATIM from the tender (you may join lines that were wrapped).
  Never paraphrase, never add numbers that are not in the text.
- One item per bullet, value or clause. Keep a range as ONE requirement ("350 - 2980 rpm").
  Split a bullet only when its parts can be answered differently (e.g. a list of different
  supplied items such as pump, oil, pipes, control panel).
- Text in parentheses that explains a requirement belongs to that requirement.
- priority: "mandatory" for shall/required/minimum/rated-for statements; "desirable" for
  preferred / may be considered / optional / future; "informational" for operating-profile or
  context statements (test duration, starts per day, application, layout) that describe the
  intended use rather than a product feature.
- category: technical, testing, documentation, commercial, warranty, or other.
- Ignore table headers, form instructions and column titles (Sr. No, Technical Compliance...).
- clause_ref: the item/section the clause sits under, e.g. "Item 1 - Gearbox" or "Item 2 - Warranty".
- Text may be broken across table rows or pages; treat it as one continuous document.
"""


def extract_requirements(llm, tender_text: str, max_chars: int = 10000) -> list[Requirement]:
    parts = split_lines_to_chunks(tender_text.splitlines(), max_chars)
    raw: list[RawRequirement] = []
    for part in parts:
        data = llm.generate_json(f"TENDER TEXT:\n\n{part}", SYSTEM, list[RawRequirement])
        for item in data:
            raw.append(RawRequirement.model_validate(item))

    norm_tender = normalize(tender_text)
    seen, reqs = set(), []
    for r in raw:
        key = normalize(r.clause_text)
        if not key or key in seen:
            continue
        seen.add(key)
        note = ""
        if key not in norm_tender:
            ratio = SequenceMatcher(None, key, norm_tender, autojunk=False).find_longest_match(
                0, len(key), 0, len(norm_tender)).size / len(key)
            if ratio < 0.85:
                note = "Clause text not found verbatim in tender - check extraction"
        reqs.append(Requirement(**r.model_dump(), req_id=f"R-{len(reqs) + 1:03d}", extraction_note=note))
    return reqs
