"""Plain-function S0-S6 orchestrator. graphs/ingestion_graph.py wraps these
same functions as LangGraph nodes; this module is what CLI `wiki ingest` and
the unit/integration tests call directly, so business logic stays testable
without spinning up a graph (see plan's LangGraph-usage decision).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from builder import metadata as metadata_mod
from builder import relations as relations_mod
from builder import relations_space as relations_space_mod
from builder import review as review_mod
from builder import translate as translate_mod
from builder import verify_curate
from builder.enrichment import enrich_document
from builder.extract.base import run_extraction
from builder.intake import run_intake
from builder.structuring import extract_title, structure_document
from core import manifest as manifest_io
from core.ids import resolve_doc_id
from core.progress import NULL_REPORTER, StageReporter
from core.providers import LLMProvider
from core.schemas import ExtractedDoc, WikiFrontmatter
from core.wiki_io import WikiIndex, load_index, neighbor_candidates


def _write_staging(staging_root: Path, source_id: str, subdir: str, filename: str, content: str) -> Path:
    p = Path(staging_root) / source_id / subdir / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _structure_and_translate(
    doc: ExtractedDoc,
    llm: LLMProvider,
    translate_enabled: bool,
    regen_hint: str | None = None,
    reporter: StageReporter = NULL_REPORTER,
) -> tuple[str, list[str]]:
    """S2 (Normalize) followed by S2.5 (Translate, English-only, conditional)
    -- kept together since both call sites that (re)generate structured_md
    (initial + S5.5 regen loop) must translate the result before it flows
    into S3 enrich.
    """
    reporter.start("structure", doc.doc_id)
    structured_md, flags = structure_document(doc, llm, regen_hint=regen_hint)
    reporter.finish("structure", doc.doc_id)
    if not translate_enabled:
        return structured_md, flags
    reporter.start("translate", doc.doc_id)
    structured_md, translate_flags = translate_mod.translate_document(structured_md, llm)
    reporter.finish("translate", doc.doc_id)
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
    reporter: StageReporter = NULL_REPORTER,
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

    structured_md, structure_flags = _structure_and_translate(doc, llm, translate_enabled, reporter=reporter)
    reporter.start("enrich", doc.doc_id)
    enrichment = enrich_document(doc.doc_id, structured_md, llm, entity_types)
    reporter.finish("enrich", doc.doc_id)

    attempt = 1
    while True:
        _write_staging(paths["staging"], intake_result.source_id, "02_structured", f"{doc.doc_id}.md", structured_md)
        _write_staging(
            paths["staging"], intake_result.source_id, "03_enrichment", f"{doc.doc_id}.json",
            enrichment.model_dump_json(indent=2),
        )

        reporter.start("metadata", doc.doc_id)
        display_title = extract_title(structured_md) or doc.title
        fm = metadata_mod.assemble_frontmatter(
            doc, enrichment, intake_result, doc_id, slug, index, display_title, existing=existing
        )
        reporter.finish("metadata", doc.doc_id)

        reporter.start("relations", doc.doc_id)
        raw_relations = relations_mod.map_relations(doc, fm, index)
        reporter.finish("relations", doc.doc_id)

        neighbor_ids = neighbor_candidates(index, fm)
        reporter.start("verify", f"{doc.doc_id} attempt {attempt}")
        report = verify_curate.verify_and_curate(
            doc, structured_md, enrichment, fm, raw_relations, llm, relation_types, neighbor_ids, attempt
        )
        reporter.finish("verify", f"{doc.doc_id} attempt {attempt}")
        _write_staging(
            paths["staging"], intake_result.source_id, "06_verification", f"{doc.doc_id}.json",
            report.model_dump_json(indent=2),
        )

        if report.verdict != "regenerate" or attempt >= max_regen:
            break

        hint = verify_curate.build_regen_hint(report)
        if report.completeness:
            structured_md, structure_flags = _structure_and_translate(
                doc, llm, translate_enabled, regen_hint=hint, reporter=reporter
            )
        reporter.start("enrich", f"{doc.doc_id} regen")
        enrichment = enrich_document(doc.doc_id, structured_md, llm, entity_types, regen_hint=hint)
        reporter.finish("enrich", f"{doc.doc_id} regen")
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

    reporter.start("draft", doc.doc_id)
    body = review_mod.append_review_flags(structured_md, [*structure_flags, *enrichment.review_flags])
    body = verify_curate.annotate_body(body, report)
    review_mod.write_draft(paths["wiki_draft"], fm, body)
    reporter.finish("draft", doc.doc_id)

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
    reporter: StageReporter = NULL_REPORTER,
) -> list[str]:
    """Runs S0-S6 for every ExtractedDoc produced from `path`. Returns the
    doc_ids that now have a draft on disk (decision E: a no-op re-ingest of an
    unchanged, fully-processed source just returns the previously recorded
    doc_ids instead of redoing work, unless force=True).
    """
    reporter.start("intake", str(path))
    intake_result = run_intake(path, paths["raw"], paths["staging"])
    manifest = manifest_io.load_or_init(paths["staging"], intake_result.source_id, str(path))
    reporter.finish("intake", str(path))

    if not force and not intake_result.changed and manifest_io.resume_step(manifest) is None:
        return manifest.doc_ids

    try:
        reporter.start("extract", str(path))
        extraction = run_extraction(
            intake_result.raw_path,
            intake_result.source_id,
            intake_result.source_type,
            paths["staging"],
            title_hint=Path(intake_result.original_path).stem,
        )
        manifest_io.mark(manifest, "extract", "done")
        reporter.finish("extract", str(path))

        index = load_index(paths["wiki_approved"])
        doc_ids: list[str] = []
        for doc in extraction.documents:
            doc_ids.append(
                process_document(
                    doc, intake_result, paths, llm, entity_types, index, relation_types, max_regen,
                    translate_enabled, reporter=reporter,
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


@dataclass
class BuildResult:
    """S0-S6 result for one ExtractedDoc -- see build_document/run_build."""

    doc_id: str
    frontmatter: WikiFrontmatter
    structured_md: str
    review_flags: list[str]


def build_document(
    doc: ExtractedDoc,
    intake_result,
    paths: dict[str, Any],
    llm: LLMProvider,
    entity_types: list[str],
    index: WikiIndex,
    translate_enabled: bool = True,
    reporter: StageReporter = NULL_REPORTER,
) -> BuildResult:
    """S0-S6, minus S5.5 (extension, additive): mirrors process_document's
    doc_id resolution and staging writes exactly, but skips S5.5 verification/
    curation and its regen loop -- `frontmatter.relations` is therefore S5's
    raw/uncurated output, never narrowed or annotated. Still writes a S6
    draft file (review_mod.write_draft), so the result IS visible to
    `wiki review list` / `wiki approve` same as a normal ingest, just without
    S5.5's verification pass.

    Relation mapping also differs from process_document: this uses
    relations_space_mod.map_relations_by_space (space_id-scoped, see
    builder/relations_space.py) instead of relations_mod.map_relations
    (foreign_key/see_also), since /build is the space-scoped ingestion path.
    """
    existing_doc_ids = set(index.docs.keys()) | review_mod.existing_draft_doc_ids(paths["wiki_draft"])
    doc_id, slug = resolve_doc_id(doc.doc_id, doc.title, index.by_source_id, existing_doc_ids)
    existing = index.docs.get(doc_id)

    structured_md, structure_flags = _structure_and_translate(doc, llm, translate_enabled, reporter=reporter)
    reporter.start("enrich", doc.doc_id)
    enrichment = enrich_document(doc.doc_id, structured_md, llm, entity_types)
    reporter.finish("enrich", doc.doc_id)

    _write_staging(paths["staging"], intake_result.source_id, "02_structured", f"{doc.doc_id}.md", structured_md)
    _write_staging(
        paths["staging"], intake_result.source_id, "03_enrichment", f"{doc.doc_id}.json",
        enrichment.model_dump_json(indent=2),
    )

    reporter.start("metadata", doc.doc_id)
    display_title = extract_title(structured_md) or doc.title
    fm = metadata_mod.assemble_frontmatter(
        doc, enrichment, intake_result, doc_id, slug, index, display_title, existing=existing
    )
    reporter.finish("metadata", doc.doc_id)

    reporter.start("relations", doc.doc_id)
    raw_relations = relations_space_mod.map_relations_by_space(fm, index)
    reporter.finish("relations", doc.doc_id)
    fm = fm.model_copy(update={"relations": raw_relations})

    # Register immediately, same reasoning as process_document: a multi-table
    # sqlite source processes several ExtractedDocs in one run, and later
    # tables' FK/see_also relations need to see earlier tables already in the
    # index.
    index.add_document(fm)

    review_flags = [*structure_flags, *enrichment.review_flags]

    reporter.start("draft", doc.doc_id)
    draft_body = review_mod.append_review_flags(structured_md, review_flags)
    review_mod.write_draft(paths["wiki_draft"], fm, draft_body)
    reporter.finish("draft", doc.doc_id)

    return BuildResult(doc_id=doc_id, frontmatter=fm, structured_md=structured_md, review_flags=review_flags)


def run_build(
    path: Path,
    paths: dict[str, Any],
    llm: LLMProvider,
    entity_types: list[str],
    translate_enabled: bool = True,
    reporter: StageReporter = NULL_REPORTER,
) -> list[BuildResult]:
    """Runs S0-S6 for every ExtractedDoc produced from `path`, skipping only
    S5.5 verification/curation, and returns their frontmatter/body/
    review_flags directly. A S6 draft file is written for each result (same
    as run_ingest), so `wiki review list` / `wiki approve` can pick them up.
    Unlike run_ingest, there is no `force` flag and no skip-if-unchanged
    short-circuit: a build call always (re)does the work.
    """
    reporter.start("intake", str(path))
    intake_result = run_intake(path, paths["raw"], paths["staging"])
    manifest = manifest_io.load_or_init(paths["staging"], intake_result.source_id, str(path))
    reporter.finish("intake", str(path))

    try:
        reporter.start("extract", str(path))
        extraction = run_extraction(
            intake_result.raw_path,
            intake_result.source_id,
            intake_result.source_type,
            paths["staging"],
            title_hint=Path(intake_result.original_path).stem,
        )
        manifest_io.mark(manifest, "extract", "done")
        reporter.finish("extract", str(path))

        index = load_index(paths["wiki_approved"])
        results: list[BuildResult] = [
            build_document(doc, intake_result, paths, llm, entity_types, index, translate_enabled, reporter=reporter)
            for doc in extraction.documents
        ]

        # Each build_document call above already wrote a S6 draft file, so
        # (unlike the old S0-S5-only behavior) "draft" is genuinely done and
        # manifest.doc_ids reflects real drafts on disk -- same contract
        # run_ingest documents. A later `wiki ingest` on this same path will
        # therefore see resume_step(manifest) is None and, absent --force,
        # short-circuit to these same doc_ids instead of redoing the work.
        for step in ("structure", "translate", "enrich", "metadata", "relations", "draft"):
            manifest_io.mark(manifest, step, "done")
        manifest.doc_ids = [r.doc_id for r in results]
    except Exception as exc:
        manifest_io.mark(manifest, manifest_io.resume_step(manifest) or "draft", "failed", detail=str(exc))
        manifest_io.save(paths["staging"], manifest)
        raise
    else:
        manifest_io.save(paths["staging"], manifest)

    return results
