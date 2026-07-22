"""Pydantic contracts shared across pipeline stages (S0-S8).

Note: the Enrichment model (S3 output) intentionally has NO chunk_context field.
chunk_context is assembled later, at S7, from section_summaries + the actual
chunker output -- see builder/indexing/embedder.py. Predicting it earlier risks
drifting from the real (section_path, chunk_idx) pairs the chunker produces.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SourceType = Literal["pdf", "csv", "txt", "md", "sqlite"]
BlockType = Literal["heading", "paragraph", "table", "code"]
ReviewStatus = Literal["draft", "approved", "rejected"]
RelationType = Literal[
    "references", "parent_of", "see_also", "foreign_key", "linked_document"
]


class Block(BaseModel):
    type: BlockType
    text: str | None = None
    level: int | None = None
    header: list[str] | None = None
    rows: list[list[str]] | None = None
    caption: str | None = None
    page: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ExtractedDoc(BaseModel):
    doc_id: str
    source_id: str
    source_type: SourceType
    title: str
    blocks: list[Block] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    """Top-level shape of staging/{source_id}/01_extracted.json"""

    source_id: str
    source_type: SourceType
    documents: list[ExtractedDoc] = Field(default_factory=list)


class SectionSummary(BaseModel):
    section_path: str
    summary: str


class EntityMention(BaseModel):
    section_path: str | None = None
    quote: str | None = None


class Entity(BaseModel):
    name: str
    type: str
    canonical: str | None = None
    aliases: list[str] = Field(default_factory=list)
    mentions: list[EntityMention] = Field(default_factory=list)


class Enrichment(BaseModel):
    """S3 output. No chunk_context here -- see module docstring."""

    doc_id: str
    doc_summary: str
    section_summaries: list[SectionSummary] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)


class SourceRef(BaseModel):
    source_id: str
    raw_path: str
    type: SourceType
    hash: str
    locator: dict[str, Any] = Field(default_factory=dict)


class Relation(BaseModel):
    type: RelationType
    target: str


class WikiFrontmatter(BaseModel):
    """S4-assembled frontmatter. Deterministic assembly, no LLM call."""

    id: str
    title: str
    slug: str
    source: SourceRef
    doc_type: str = "generic"
    summary: str
    keywords: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    review_status: ReviewStatus = "draft"
    version: int = 1
    created_at: datetime
    updated_at: datetime | None = None
    reviewed_by: str | None = None


class RawChunk(BaseModel):
    section_path: str
    chunk_idx: int
    text: str
    source_page: int | None = None


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    section_path: str
    chunk_idx: int
    text: str
    source_page: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


# --- S5.5 - Verification & Relation Curation (extension, additive only) ---
# See LLM_WIKI_Builder_확장_검증_및_관계큐레이션.md. These models are consumed by
# builder/verify_curate.py and never change anything above this line.


class Finding(BaseModel):
    claim: str
    grounded: bool
    evidence: str | None = None
    severity: Literal["low", "med", "high"]


class ValueChange(BaseModel):
    """S5.5 #3 (extension, additive): a number/SQL/command/config value that
    appears in BOTH the original and the generated text but with a different
    value -- distinct from Finding, which is presence/absence (hallucination),
    not value fidelity. No severity field: unlike Finding, this is never
    LLM-judged for consequence -- compute_verdict always treats a non-empty
    value_changes list as "regenerate" (decision V6).
    """

    kind: Literal["number", "sql", "command", "config"]
    original_value: str
    changed_value: str
    evidence: str | None = None


class RelationSuggestion(BaseModel):
    """A pre-validation suggestion from S5.5's relation curation. `type` is a
    plain str (not the RelationType Literal) because it's unvalidated LLM
    output -- builder/verify_curate.py::apply_curation checks it against the
    configured allowed types before ever constructing a real Relation.
    """

    action: Literal["keep", "prune", "add"]
    type: str
    target: str
    confidence: float
    rationale: str
    status: Literal["proposed"] = "proposed"


class VerificationReport(BaseModel):
    doc_id: str
    verdict: Literal["pass", "regenerate", "review"]
    score: float
    attempt: int
    faithfulness: list[Finding] = Field(default_factory=list)
    completeness: list[str] = Field(default_factory=list)
    value_changes: list[ValueChange] = Field(default_factory=list)
    schema_issues: list[str] = Field(default_factory=list)
    relations: list[RelationSuggestion] = Field(default_factory=list)


class ReviewAgentReport(BaseModel):
    """Review Agent (external-facing, read-only) output: supports a human's
    approval decision on a Wiki Draft. Unlike VerificationReport, this never
    drives relation curation or a regeneration loop -- recommendation is
    computed deterministically from the same grounding findings
    (verify_curate.compute_verdict), never trusted from LLM self-report
    (decision V6). Mirrors compute_verdict's three-way pass/review/regenerate
    split: approve = no issues found, review = minor issues a human should
    look over but that don't block approval, revise = issues serious enough
    that the draft should be corrected before approval.
    """

    doc_id: str
    recommendation: Literal["Approve", "Review", "Revise"]
    faithfulness: list[Finding] = Field(default_factory=list)
    completeness: list[str] = Field(default_factory=list)
    value_changes: list[ValueChange] = Field(default_factory=list)
    review_comment: str
