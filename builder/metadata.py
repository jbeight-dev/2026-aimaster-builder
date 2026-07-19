"""S4 - Metadata Assembly. Deterministic: takes S3's Enrichment output plus
source provenance and assembles the WikiFrontmatter. No LLM call here.

Entity canonical resolution (decision D) also happens here, against the
in-memory WikiIndex: a name/alias that matches an existing canonical reuses
it; anything new mints a fresh canonical (first-seen-wins) and is logged to
index.unresolved_entities for human review rather than created silently.
"""
from __future__ import annotations

from datetime import datetime, timezone

from builder.intake import IntakeResult
from core.ids import slugify
from core.schemas import Entity, ExtractedDoc, Enrichment, SourceRef, SourceType, WikiFrontmatter
from core.wiki_io import WikiIndex

_DATASET_SOURCE_TYPES: set[SourceType] = {"csv", "sqlite"}


def infer_doc_type(source_type: SourceType) -> str:
    """Classification only -- every source_type runs the same pipeline
    (architectural invariant #3). This label never branches processing.
    """
    return "dataset" if source_type in _DATASET_SOURCE_TYPES else "generic"


def locator_for(doc: ExtractedDoc) -> dict:
    if doc.source_type == "pdf":
        pages = sorted({b.page for b in doc.blocks if b.page is not None})
        return {"pages": pages} if pages else {}
    if doc.source_type == "sqlite":
        return {"table": doc.title}
    return {}


def match_entities(entities: list[Entity], index: WikiIndex) -> list[Entity]:
    resolved: list[Entity] = []
    for entity in entities:
        canonical = index.resolve_entity(entity.name, entity.aliases)
        if canonical is None:
            canonical = slugify(entity.name)
            index.register_entity(entity.name, canonical, entity.aliases)
            index.unresolved_entities.append(
                {"name": entity.name, "type": entity.type, "canonical": canonical}
            )
        else:
            index.register_entity(entity.name, canonical, entity.aliases)
        resolved.append(entity.model_copy(update={"canonical": canonical}))
    return resolved


def assemble_frontmatter(
    doc: ExtractedDoc,
    enrichment: Enrichment,
    intake: IntakeResult,
    doc_id: str,
    slug: str,
    index: WikiIndex,
    display_title: str,
    existing: WikiFrontmatter | None = None,
) -> WikiFrontmatter:
    now = datetime.now(timezone.utc)
    resolved_entities = match_entities(enrichment.entities, index)

    source_ref = SourceRef(
        # doc.doc_id (not the intake-level source_id) is the identity used for
        # re-ingestion matching: for non-sqlite formats it equals source_id,
        # but sqlite produces one ExtractedDoc per table under a shared
        # intake source_id, so the per-table id is what must stay unique in
        # WikiIndex.by_source_id (raw_path below still points at the single
        # shared raw file/db, independent of this).
        source_id=doc.doc_id,
        raw_path=intake.raw_path,
        type=intake.source_type,
        hash=intake.content_hash,
        locator=locator_for(doc),
    )

    return WikiFrontmatter(
        id=doc_id,
        title=display_title,
        slug=slug,
        source=source_ref,
        doc_type=infer_doc_type(intake.source_type),
        summary=enrichment.doc_summary,
        keywords=enrichment.keywords,
        concepts=enrichment.concepts,
        tags=[],
        entities=resolved_entities,
        relations=list(existing.relations) if existing else [],
        review_status="draft",
        version=(existing.version + 1) if existing else 1,
        created_at=existing.created_at if existing else now,
        updated_at=now,
        reviewed_by=existing.reviewed_by if existing else None,
    )
