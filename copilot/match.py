"""For each requirement: retrieve evidence, ask the LLM for a structured verdict, verify it."""
from __future__ import annotations

from .models import Chunk, MatchResult, RawMatch, Requirement
from .retrieve import Index
from .verify import verify

SYSTEM = """You are a technical pre-sales engineer checking whether a supplier's offered product meets ONE
tender requirement. Use ONLY the numbered supplier-document excerpts provided. Never use outside
knowledge, never infer a value that is not written, never do engineering calculations to "prove" compliance.

Status definitions
- Compliant: excerpts explicitly state a value or feature that fully satisfies the requirement (every part of it).
- Partial: part of the requirement is met, or it is met only with a caveat: TBC/TBD, a different standard, a
  different arrangement, an optional item, or part of it listed as not included.
- Non-compliant: excerpts explicitly contradict the requirement, or list the required item as not included/excluded.
- Not found: the excerpts say nothing about the requirement.
- Needs human review: evidence is ambiguous or conflicting, depends on a calculation or assumption, or you are unsure.

Rules
1. Ranges: both ends must be covered by explicit statements. A stated maximum alone does not prove the minimum.
2. Compare units and powers of ten carefully (kW vs W, mbar exponents, nm vs um, rpm vs Hz).
3. A different standard (e.g. ISO 6011 vs AGMA 6011) is not the same standard: use Partial or Needs human review.
4. Exclusion lists ("not included", "excluded", "by others", "customer scope") are evidence. If the requirement needs
   an item listed there, it is Non-compliant (or Partial if only part of the requirement is affected).
5. Text that merely repeats the tender's own wording (a compliance form or appendix reproduced in the supplier
   document) and the supplier's own YES/NO in it are claims, NOT evidence. Evidence must be the supplier's technical
   description. Excerpts marked SUPPLIER COMPLIANCE FORM must not be used as evidence.
6. evidence_quote: verbatim from ONE excerpt, at most 25 words, with that excerpt's id in evidence_chunk_id.
   For Not found leave both empty.
7. offered_value: the value exactly as written in the evidence, with units; empty if none.
8. For "informational" requirements (intended use, duty profile), judge whether the offer is consistent with them;
   use Not found if the offer does not address them.
9. confidence is 0 to 1; use below 0.6 when unsure. Keep reasoning to 1-2 sentences.
"""


def build_prompt(req: Requirement, chunks: list[Chunk], product: str) -> str:
    parts = [
        "TENDER REQUIREMENT",
        f"ID: {req.req_id}",
        f"Section: {req.clause_ref}",
        f"Priority: {req.priority}",
        f"Parameter: {req.parameter}",
        f"Required value: {req.required_value or '(qualitative)'}",
        f"Text: {req.clause_text}",
        "",
        f"SUPPLIER DOCUMENT EXCERPTS (product: {product})",
    ]
    for c in chunks:
        flag = " [SUPPLIER COMPLIANCE FORM - restates the tender, NOT evidence]" if c.is_form else ""
        parts.append(f"[{c.id}] (file: {c.source_file}, page {c.page}, section: {c.section or '-'}){flag}\n{c.text}")
    return "\n".join(parts)


def match_requirement(llm, req: Requirement, index: Index, k: int = 6) -> MatchResult:
    chunks = index.search(f"{req.parameter}. {req.clause_text}", k=k)
    shown = {c.id: c for c in chunks}
    try:
        data = llm.generate_json(build_prompt(req, chunks, index.product), SYSTEM, RawMatch)
        raw = RawMatch.model_validate(data)
    except Exception as e:  # malformed JSON, schema error, etc. -> never guess
        return MatchResult(status="Needs human review", offered_value="", evidence_chunk_id="",
                           evidence_quote="", reasoning="LLM output could not be parsed.",
                           confidence=0.0, req_id=req.req_id, product=index.product,
                           verifier_note=f"LLM error: {str(e)[:120]}")
    return verify(raw, req, shown, index.product)
