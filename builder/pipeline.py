"""Plain-function S0-S6 orchestrator. graphs/ingestion_graph.py wraps these
same functions as LangGraph nodes; this module is what CLI `wiki ingest` and
the unit/integration tests call directly, so business logic stays testable
without spinning up a graph (see plan's LangGraph-usage decision).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from builder import metadata as metadata_mod
from builder import relations as relations_mod
from builder import review as review_mod
from builder import translate as translate_mod
from builder import verify_curate
from builder.enrichment import enrich_document
from builder.extract.base import run_extraction
from builder.intake import run_intake
from builder.structuring import structure_document
from core import manifest as manifest_io
from core.ids import resolve_doc_id
from core.providers import LLMProvider
from core.schemas import ExtractedDoc
from core.wiki_io import WikiIndex, load_index, neighbor_candidates


def _write_staging(staging_root: Path, source_id: str, subdir: str, filename: str, content: str) -> Path:
    p = Path(staging_root) / source_id / subdir / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _structure_and_translate(
    doc: ExtractedDoc, llm: LLMProvider, translate_enabled: bool, regen_hint: str | None = None
) -> tuple[str, list[str]]:
    """S2 (Normalize) followed by S2.5 (Translate, English-only, conditional)
    -- kept together since both call sites that (re)generate structured_md
    (initial + S5.5 regen loop) must translate the result before it flows
    into S3 enrich.
    """
    structured_md, flags = structure_document(doc, llm, regen_hint=regen_hint)
    if not translate_enabled:
        return structured_md, flags
    structured_md, translate_flags = translate_mod.translate_document(structured_md, llm)
    return structured_md, flags + translate_flags


def process_document(
    doc: ExtractedDoc,
    intake_result,
    paths: dict[str, Any],
    llm: LLMProvider,
    entity_types: list[str],
    index: WikiIndex,
    relation_types: list[str] | None = None,
    max_regen: int = 2,
    translate_enabled: bool = True,
) -> str:
    """relation_types/max_regen (extension, additive): drive S5.5 verification
    + relation curation (builder/verify_curate.py), which runs after S5 on
    every call. Defaults preserve every existing call site's behavior exactly
    under FakeLLMProvider (see verify_curate module docstring) while still
    genuinely executing the new stage.

    translate_enabled (extension, additive): gates S2.5 (builder/translate.py),
    which only ever calls the LLM for English input, so True is a safe default.
    """
    relation_types = relation_types or []

    # Collision candidates must include pending drafts, not just approved docs:
    # a second not-yet-approved document that slugifies to the same doc_id
    # would otherwise silently overwrite the first one's draft file (only
    # load_index(wiki_approved) is known to `index` at this point). doc_id/slug
    # are resolved once, up front, since they must stay stable across any
    # S5.5-triggered regeneration attempts below (only structured_md/enrichment
    # change on regeneration, never doc.title/doc.doc_id).
    existing_doc_ids = set(index.docs.keys()) | review_mod.existing_draft_doc_ids(paths["wiki_draft"])
    doc_id, slug = resolve_doc_id(doc.doc_id, doc.title, index.by_source_id, existing_doc_ids)
    existing = index.docs.get(doc_id)

    structured_md, structure_flags = _structure_and_translate(doc, llm, translate_enabled)
    enrichment = enrich_document(doc.doc_id, structured_md, llm, entity_types)

    attempt = 1
    while True:
        _write_staging(paths["staging"], intake_result.source_id, "02_structured", f"{doc.doc_id}.md", structured_md)
        _write_staging(
            paths["staging"], intake_result.source_id, "03_enrichment", f"{doc.doc_id}.json",
            enrichment.model_dump_json(indent=2),
        )

        fm = metadata_mod.assemble_frontmatter(doc, enrichment, intake_result, doc_id, slug, index, existing=existing)
        raw_relations = relations_mod.map_relations(doc, fm, index)

        neighbor_ids = neighbor_candidates(index, fm)
        report = verify_curate.verify_and_curate(
            doc, structured_md, enrichment, fm, raw_relations, llm, relation_types, neighbor_ids, attempt
        )
        _write_staging(
            paths["staging"], intake_result.source_id, "06_verification", f"{doc.doc_id}.json",
            report.model_dump_json(indent=2),
        )

        if report.verdict != "regenerate" or attempt >= max_regen:
            break

        hint = verify_curate.build_regen_hint(report)
        if report.completeness:
            structured_md, structure_flags = _structure_and_translate(doc, llm, translate_enabled, regen_hint=hint)
        enrichment = enrich_document(doc.doc_id, structured_md, llm, entity_types, regen_hint=hint)
        attempt += 1

    valid_targets = set(neighbor_ids) | {r.target for r in raw_relations}
    curated_relations, _ = verify_curate.apply_curation(
        raw_relations, report.relations, valid_targets, set(relation_types)
    )
    fm = fm.model_copy(update={"relations": curated_relations})

    # Register immediately: a multi-table sqlite source processes several
    # ExtractedDocs in one run, and later tables' FK/see_also relations need
    # to see earlier tables already in the index.
    index.add_document(fm)

    body = review_mod.append_review_flags(structured_md, [*structure_flags, *enrichment.review_flags])
    body = verify_curate.annotate_body(body, report)
    review_mod.write_draft(paths["wiki_draft"], fm, body)

    return doc_id


def run_ingest(
    path: Path,
    paths: dict[str, Any],
    llm: LLMProvider,
    entity_types: list[str],
    force: bool = False,
    relation_types: list[str] | None = None,
    max_regen: int = 2,
    translate_enabled: bool = True,
) -> list[str]:
    """Runs S0-S6 for every ExtractedDoc produced from `path`. Returns the
    doc_ids that now have a draft on disk (decision E: a no-op re-ingest of an
    unchanged, fully-processed source just returns the previously recorded
    doc_ids instead of redoing work, unless force=True).
    """
    intake_result = run_intake(path, paths["raw"], paths["staging"])
    manifest = manifest_io.load_or_init(paths["staging"], intake_result.source_id, str(path))

    if not force and not intake_result.changed and manifest_io.resume_step(manifest) is None:
        return manifest.doc_ids

    try:
        extraction = run_extraction(
            intake_result.raw_path,
            intake_result.source_id,
            intake_result.source_type,
            paths["staging"],
            title_hint=Path(intake_result.original_path).stem,
        )
        manifest_io.mark(manifest, "extract", "done")

        index = load_index(paths["wiki_approved"])
        doc_ids: list[str] = []
        for doc in extraction.documents:
            doc_ids.append(
                process_document(
                    doc, intake_result, paths, llm, entity_types, index, relation_types, max_regen,
                    translate_enabled,
                )
            )

        for step in ("structure", "translate", "enrich", "metadata", "relations", "draft"):
            manifest_io.mark(manifest, step, "done")
        manifest.doc_ids = doc_ids
    except Exception as exc:
        manifest_io.mark(manifest, manifest_io.resume_step(manifest) or "draft", "failed", detail=str(exc))
        manifest_io.save(paths["staging"], manifest)
        raise
    else:
        manifest_io.save(paths["staging"], manifest)

    return doc_ids
