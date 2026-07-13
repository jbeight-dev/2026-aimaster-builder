"""Higher-level operations that orchestrate several builder/* modules for a
single doc_id (verify) or a batch of them (relink). Both `cli.py` and
`api/app.py` call these so the two front ends share one code path -- unlike
the other CLI commands, verify/relink involve enough orchestration that it
doesn't belong inlined in a thin CLI handler.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from builder import finalize as finalize_mod
from builder import review as review_mod
from builder import verify_curate
from core import wiki_io
from core.providers import LLMProvider
from core.schemas import ExtractionResult, VerificationReport, WikiFrontmatter


def run_verify(
    doc_id: str,
    paths: dict[str, Any],
    llm: LLMProvider,
    relation_types: list[str],
    neighbor_top_k: int = 8,
) -> tuple[WikiFrontmatter, VerificationReport]:
    """S5.5 standalone re-run against the current draft-or-approved doc_id,
    using its existing staging artifacts. Writes the updated frontmatter +
    annotated body back to whichever file (draft or approved) it read from.
    """
    draft_path = review_mod.draft_path(paths["wiki_draft"], doc_id)
    approved_path = Path(paths["wiki_approved"]) / f"{doc_id}.md"
    if draft_path.exists():
        target_path = draft_path
    elif approved_path.exists():
        target_path = approved_path
    else:
        raise FileNotFoundError(f"No draft or approved document found for {doc_id!r}")
    fm, body = wiki_io.read(target_path)

    intake_source_id = Path(fm.source.raw_path).parent.name
    extraction_path = paths["staging"] / intake_source_id / "01_extracted.json"
    if not extraction_path.exists():
        raise FileNotFoundError(
            f"Missing staging artifact {extraction_path}; re-ingest the source first."
        )
    extraction = ExtractionResult.model_validate_json(extraction_path.read_text(encoding="utf-8"))
    doc = next((d for d in extraction.documents if d.doc_id == fm.source.source_id), None)
    if doc is None:
        raise ValueError(f"Could not locate original extracted blocks for {fm.source.source_id!r}.")
    enrichment = finalize_mod._load_enrichment(paths["staging"], fm)

    index = wiki_io.load_index(paths["wiki_approved"])
    neighbor_ids = wiki_io.neighbor_candidates(index, fm, top_k=neighbor_top_k)

    clean_body = verify_curate.strip_previous_annotations(body)
    report = verify_curate.verify_and_curate(
        doc, clean_body, enrichment, fm, fm.relations, llm, relation_types, neighbor_ids, attempt=1
    )

    # verify_and_curate() already ran apply_curation() internally against the
    # same (raw_relations=fm.relations, valid_targets) inputs to compute
    # report.schema_issues -- re-running it here only to get new_relations
    # back out (apply_curation doesn't return them via the report).
    valid_targets = set(neighbor_ids) | {r.target for r in fm.relations}
    new_relations, _issues = verify_curate.apply_curation(
        fm.relations, report.relations, valid_targets, set(relation_types)
    )

    updated_fm = fm.model_copy(update={"relations": new_relations})
    updated_body = verify_curate.annotate_body(clean_body, report)
    wiki_io.write(target_path, updated_fm, updated_body)
    return updated_fm, report


@dataclass
class RelinkResult:
    doc_id: str
    before_count: int
    after_count: int
    changed: bool
    applied: bool
    issues: list[str] = field(default_factory=list)


def run_relink(
    target_ids: list[str],
    paths: dict[str, Any],
    llm: LLMProvider,
    relation_types: list[str],
    neighbor_top_k: int = 8,
    apply: bool = False,
) -> list[RelinkResult]:
    """Batch re-curation of relations across already-approved documents.
    Dry-run unless apply=True; no reindex is needed since relations aren't
    embedded content.
    """
    index = wiki_io.load_index(paths["wiki_approved"])
    results: list[RelinkResult] = []

    for doc_id in target_ids:
        fm = index.docs.get(doc_id)
        if fm is None:
            results.append(
                RelinkResult(doc_id=doc_id, before_count=0, after_count=0, changed=False, applied=False,
                              issues=[f"{doc_id!r} not found among approved documents"])
            )
            continue

        neighbor_ids = wiki_io.neighbor_candidates(index, fm, top_k=neighbor_top_k)
        new_relations, _suggestions, issues = verify_curate.plan_relink(fm, neighbor_ids, llm, relation_types)
        changed = new_relations != fm.relations
        applied = False

        if apply and changed:
            _, body = wiki_io.read(paths["wiki_approved"] / f"{doc_id}.md")
            updated_fm = fm.model_copy(update={"relations": new_relations, "version": fm.version + 1})
            wiki_io.write(paths["wiki_approved"] / f"{doc_id}.md", updated_fm, body)
            index.docs[doc_id] = updated_fm
            applied = True

        results.append(
            RelinkResult(
                doc_id=doc_id,
                before_count=len(fm.relations),
                after_count=len(new_relations),
                changed=changed,
                applied=applied,
                issues=issues,
            )
        )

    return results
