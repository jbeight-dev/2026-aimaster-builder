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
from builder import review_agent as review_agent_mod
from builder import verify_curate
from core import wiki_io
from core.progress import NULL_REPORTER, StageReporter
from core.providers import LLMProvider
from core.schemas import ExtractedDoc, ExtractionResult, ReviewAgentReport, VerificationReport, WikiFrontmatter


def _locate_target_and_original(
    doc_id: str, paths: dict[str, Any]
) -> tuple[Path, WikiFrontmatter, str, ExtractedDoc]:
    """Finds the draft-or-approved file for doc_id and recovers the original
    ExtractedDoc from its staging artifacts. Shared by run_verify and
    run_review_agent -- both need "the current wiki body" + "the original
    source blocks it was built from", they just do different things with them.
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
    return target_path, fm, body, doc


def run_verify(
    doc_id: str,
    paths: dict[str, Any],
    llm: LLMProvider,
    relation_types: list[str],
    neighbor_top_k: int = 8,
    reporter: StageReporter = NULL_REPORTER,
) -> tuple[WikiFrontmatter, VerificationReport]:
    """S5.5 standalone re-run against the current draft-or-approved doc_id,
    using its existing staging artifacts. Writes the updated frontmatter +
    annotated body back to whichever file (draft or approved) it read from.
    """
    reporter.start("locate", doc_id)
    target_path, fm, body, doc = _locate_target_and_original(doc_id, paths)
    reporter.finish("locate", doc_id)

    reporter.start("load_enrichment", doc_id)
    enrichment = finalize_mod._load_enrichment(paths["staging"], fm)
    reporter.finish("load_enrichment", doc_id)

    index = wiki_io.load_index(paths["wiki_approved"])
    neighbor_ids = wiki_io.neighbor_candidates(index, fm, top_k=neighbor_top_k)

    clean_body = verify_curate.strip_previous_annotations(body)
    reporter.start("verify", doc_id)
    report = verify_curate.verify_and_curate(
        doc, clean_body, enrichment, fm, fm.relations, llm, relation_types, neighbor_ids, attempt=1
    )
    reporter.finish("verify", doc_id)
    reporter.log(
        "verify",
        f"verdict={report.verdict} score={report.score:.2f} schema_issues={len(report.schema_issues)}",
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

    reporter.start("persist", doc_id)
    wiki_io.write(target_path, updated_fm, updated_body)
    reporter.finish("persist", doc_id)

    return updated_fm, report


def _plan_review_checks(doc: ExtractedDoc) -> list[str]:
    """Decides which grounding dimensions are worth reporting on for this doc.
    Faithfulness/completeness always apply; value_changes only matters when
    the source actually has numeric/tabular/config-like content that could be
    misquoted, so we skip mentioning it for plain narrative docs.
    """
    checklist = ["faithfulness", "completeness"]
    if any(
        b.type in ("table", "code") or (b.text is not None and any(ch.isdigit() for ch in b.text))
        for b in doc.blocks
    ):
        checklist.append("value_changes")
    return checklist


def run_review_agent(
    doc_id: str,
    paths: dict[str, Any],
    llm: LLMProvider,
    reporter: StageReporter = NULL_REPORTER,
) -> ReviewAgentReport:
    """Review Agent (external-facing, read-only): compares the current
    draft-or-approved Wiki body against its original source document for
    Faithfulness/Completeness/Value-Changes and produces a review report +
    narrative comment to support a human's approval decision. Unlike
    run_verify, this never mutates the draft/approved file and does no
    relation curation or regeneration -- it only reads and reports. The
    report is persisted as a staging audit artifact alongside 06_verification,
    but the wiki file itself is untouched.
    """
    current_step = "locate"
    try:
        reporter.start("locate", f"{doc_id} · MCP")
        target_path, fm, body, doc = _locate_target_and_original(doc_id, paths)
        clean_body = review_mod.strip_review_comments(body)
        reporter.finish("locate", f"{doc_id} · MCP")

        current_step = "plan"
        reporter.start("plan", f"{doc_id} · Deep Reasoning")
        checklist = _plan_review_checks(doc)
        reporter.log(
            "plan",
            f"{len(checklist)}-step plan generated: {', '.join(checklist)}. Simulating plan...",
        )
        reporter.finish("plan", f"{doc_id} · Deep Reasoning")

        current_step = "simulate"
        reporter.start("simulate", f"{doc_id} · Deep Reasoning")
        grounding = verify_curate.verify_grounding(doc, clean_body, llm)
        if "faithfulness" in checklist:
            ungrounded = [f for f in grounding.faithfulness if not f.grounded]
            high = sum(1 for f in ungrounded if f.severity == "high")
            reporter.log(
                "simulate",
                f"faithfulness: {len(ungrounded)} ungrounded ({high} high) / {len(grounding.faithfulness)} claims",
            )
        if "completeness" in checklist:
            reporter.log("simulate", f"completeness: {len(grounding.completeness)} gap(s)")
        if "value_changes" in checklist:
            reporter.log("simulate", f"value_changes: {len(grounding.value_changes)} mismatch(es)")
        reporter.finish("simulate", f"{doc_id} · Deep Reasoning")

        current_step = "verdict"
        reporter.start("verdict", doc_id)
        verdict = verify_curate.compute_verdict(
            grounding.faithfulness, grounding.completeness, grounding.value_changes, schema_issues=[]
        )
        recommendation = {"pass": "Approve", "review": "Review", "regenerate": "Revise"}[verdict]
        reporter.finish("verdict", f"{doc_id} -> {recommendation}")

        current_step = "compose"
        reporter.start("compose", doc_id)
        review_comment = review_agent_mod.compose_review_comment(
            grounding.faithfulness, grounding.completeness, grounding.value_changes
        )
        reporter.finish("compose", doc_id)

        report = ReviewAgentReport(
            doc_id=doc_id,
            recommendation=recommendation,
            faithfulness=grounding.faithfulness,
            completeness=grounding.completeness,
            value_changes=grounding.value_changes,
            review_comment=review_comment,
        )

        current_step = "persist"
        reporter.start("persist", f"{doc_id} · MCP")
        intake_source_id = Path(fm.source.raw_path).parent.name
        artifact_path = paths["staging"] / intake_source_id / "07_review_agent" / f"{doc_id}.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        reporter.finish("persist", f"{doc_id} · MCP")
    except Exception as exc:
        reporter.log(current_step, f"Review Agent 실패: {exc}", level="error")
        raise

    return report


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
