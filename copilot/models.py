from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field

Status = Literal["Compliant", "Partial", "Non-compliant", "Not found", "Needs human review"]
RunState = Literal["completed", "api_error", "not_run"]
STATUSES: list[str] = ["Compliant", "Partial", "Non-compliant", "Not found", "Needs human review"]


class Chunk(BaseModel):
    id: str
    text: str
    source_file: str
    page: int                      # 1-based PDF page index (not the printed page label)
    section: str = ""
    kind: str = "text"             # text | ocr | pdf_table | docx | legacy_doc | xlsx
    locator: str = ""
    regions: list[tuple[float, float, float, float]] = Field(default_factory=list)
    pinned: bool = False           # always shown to the matcher (e.g. "not included" lists)
    is_form: bool = False          # supplier's compliance form that restates the tender (not evidence)


class RawRequirement(BaseModel):
    """What the LLM returns for each atomic tender requirement."""
    clause_ref: str = Field(description="Item/section the clause sits under, e.g. 'Item 1 - Gearbox'")
    clause_text: str = Field(description="Verbatim text of ONE atomic requirement")
    parameter: str = Field(description="Short parameter name, e.g. 'Output speed range'")
    required_value: str = Field(description="Required value/limit with units, or '' if qualitative")
    category: Literal["technical", "testing", "documentation", "commercial", "warranty", "other"]
    priority: Literal["mandatory", "desirable", "informational"]


class Requirement(RawRequirement):
    req_id: str
    extraction_note: str = ""
    source_file: str = ""
    source_page: Optional[int] = None
    source_locator: str = ""
    source_regions: list[tuple[float, float, float, float]] = Field(default_factory=list)


class RawMatch(BaseModel):
    status: Status
    offered_value: str = Field(description="Value stated in the evidence, '' if none")
    evidence_chunk_id: str = Field(description="ID of the chunk containing the evidence, '' if none")
    evidence_quote: str = Field(description="Verbatim quote (max 25 words) from that chunk, '' if none")
    reasoning: str
    confidence: float = Field(ge=0, le=1)


class MatchResult(RawMatch):
    req_id: str
    product: str
    source_file: str = ""
    page: Optional[int] = None
    evidence_locator: str = ""
    evidence_regions: list[tuple[float, float, float, float]] = Field(default_factory=list)
    evidence_kind: str = ""
    verifier_note: str = ""
    extraction_note: str = ""
    run_state: RunState = "completed"
