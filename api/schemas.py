"""Request/response models for api/app.py. Reuses core/schemas.py pydantic
models directly wherever an endpoint's payload already matches one
(WikiFrontmatter, VerificationReport) instead of re-declaring them.
"""
from __future__ import annotations

from pydantic import BaseModel

from core.schemas import VerificationReport, WikiFrontmatter


class IngestOut(BaseModel):
    doc_ids: list[str]


class DocumentOut(BaseModel):
    document: WikiFrontmatter
    body: str


class BuildDocumentOut(BaseModel):
    doc_id: str
    document: WikiFrontmatter
    body: str
    review_flags: list[str]


class BuildOut(BaseModel):
    documents: list[BuildDocumentOut]


class DraftOut(BaseModel):
    doc_id: str
    title: str
    version: int
    review_flag_count: int


class IndexStatusOut(BaseModel):
    approved_documents: int
    pending_drafts: int
    collections: dict[str, int]


class ReindexPreviewOut(BaseModel):
    doc_id: str
    would_delete_existing_points: bool
    would_upsert_summary_points: int
    would_upsert_chunk_points: int


class VerifyOut(BaseModel):
    document: WikiFrontmatter
    report: VerificationReport


class RelinkRequest(BaseModel):
    doc_id: str | None = None
    all: bool = False
    apply: bool = False


class RelinkResultOut(BaseModel):
    doc_id: str
    before_count: int
    after_count: int
    changed: bool
    applied: bool
    issues: list[str]
