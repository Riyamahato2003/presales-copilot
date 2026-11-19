"""Programmatic checks on the LLM's answer. The LLM proposes; this module disposes.

A row can only stay Compliant / Partial / Non-compliant if
  1. the cited chunk was actually shown to the model,
  2. the quoted evidence really appears in that chunk,
  3. the quote is not just the tender requirement echoed back,
  4. every number in `offered_value` appears in the cited chunk.
Otherwise it is downgraded to "Needs human review" with a note saying why.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from .models import Chunk, MatchResult, RawMatch, Requirement
from .textutil import normalize, numbers_in

ASSERTIVE = {"Compliant", "Partial", "Non-compliant"}


def quote_in_text(quote: str, text: str, threshold: float = 0.9) -> bool:
    q, t = normalize(quote), normalize(text)
    if not q:
        return False
    # Fuzzy matching must never excuse changing a technical number.
    if not numbers_in(quote).issubset(numbers_in(text)):
        return False
    if q in t:
        return True
    matched = sum(b.size for b in SequenceMatcher(None, q, t, autojunk=False).get_matching_blocks())
    return matched / len(q) >= threshold


def verify(raw: RawMatch, req: Requirement, shown: dict[str, Chunk], product: str) -> MatchResult:
    res = MatchResult(**raw.model_dump(), req_id=req.req_id, product=product)
    chunk = shown.get(raw.evidence_chunk_id)
    if chunk:
        res.source_file, res.page = chunk.source_file, chunk.page
    notes: list[str] = []

    if raw.status in ASSERTIVE:
        if not chunk:
            notes.append("cited chunk id was not among the excerpts provided")
        elif not quote_in_text(raw.evidence_quote, chunk.text):
            notes.append("quoted evidence not found in the cited excerpt")
        else:
            if chunk.is_form:
                notes.append("evidence comes from the supplier's compliance form, which restates the tender")
            if SequenceMatcher(None, normalize(raw.evidence_quote), normalize(req.clause_text)).ratio() > 0.8:
                notes.append("evidence just restates the tender requirement")
            quote_numbers = numbers_in(raw.evidence_quote)
            missing_quote_numbers = quote_numbers - numbers_in(chunk.text)
            if missing_quote_numbers:
                notes.append(f"evidence quote contains numbers absent from the source: {sorted(missing_quote_numbers)}")
            missing = numbers_in(raw.offered_value) - numbers_in(chunk.text)
            if missing:
                notes.append(f"offered value contains numbers not in the cited excerpt: {sorted(missing)}")
            required_numbers = numbers_in(req.required_value or req.clause_text)
            missing_required = required_numbers - quote_numbers
            if raw.status == "Compliant" and missing_required:
                notes.append(f"evidence does not explicitly cover all required numbers: {sorted(missing_required)}")
        if raw.status == "Compliant" and raw.confidence < 0.6:
            notes.append(f"low confidence ({raw.confidence:.2f}) for a Compliant verdict")

    if notes:
        res.verifier_note = "; ".join(notes)
        if raw.status in ASSERTIVE:
            res.status = "Needs human review"
            res.verifier_note = f"Downgraded from {raw.status}: " + res.verifier_note
    return res
