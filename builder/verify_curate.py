"""S5.5 - Verification & Relation Curation (extension, additive).

See LLM_WIKI_Builder_확장_검증_및_관계큐레이션.md. Sits between S5 (raw relation
mapping, builder/relations.py -- unchanged) and S6 (review/draft write):

  6-A Grounded Verification: check the S2 body + S3 enrichment against the
      original S1 blocks for hallucinations (faithfulness) and omissions
      (completeness). Verdict (pass/regenerate/review) and score are computed
      deterministically in code from the LLM's structured findings, never
      trusted from a self-reported LLM verdict (decision V6).
  6-B Relation Curation: ask the LLM to keep/prune S5's raw relations and
      propose new ones from a neighbor-document candidate list (core/wiki_io.py
      ::neighbor_candidates). Per project decision, the curated result is
      applied immediately once verification passes -- there is no separate
      manual-approval step for an individual document's own relations.

FakeLLMProvider's fallback contexts here are deliberately "nothing wrong" /
"keep everything unchanged" -- under Fake mode this whole stage is a no-op
that still genuinely executes (see module docstring in builder/pipeline.py).
"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from builder.structuring import render_blocks_naive
from core.providers import LLMProvider
from core.schemas import (
    Enrichment,
    ExtractedDoc,
    Finding,
    Relation,
    RelationSuggestion,
    ValueChange,
    VerificationReport,
    WikiFrontmatter,
)

GROUNDING_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "verification.md"
CURATION_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "relation_curation.md"

_S55_COMMENT_RE = re.compile(r"<!--\s*S5\.5[^:]*:.*?-->", re.DOTALL)


class _GroundingOutput(BaseModel):
    faithfulness: list[Finding] = Field(default_factory=list)
    completeness: list[str] = Field(default_factory=list)
    value_changes: list[ValueChange] = Field(default_factory=list)


class _CurationOutput(BaseModel):
    relations: list[RelationSuggestion] = Field(default_factory=list)


def check_schema_issues(fm: WikiFrontmatter, enrichment: Enrichment) -> list[str]:
    """6-A #3: structural/schema checks are deterministic code, not an LLM
    judgment call (decision V6).
    """
    issues: list[str] = []
    if not fm.summary.strip():
        issues.append("summary is empty")
    if not enrichment.section_summaries:
        issues.append("no section summaries were produced")
    for entity in fm.entities:
        if not entity.canonical:
            issues.append(f"entity {entity.name!r} has no canonical assigned")
    return issues


def compute_verdict(
    faithfulness: list[Finding],
    completeness: list[str],
    value_changes: list[ValueChange],
    schema_issues: list[str],
) -> str:
    ungrounded = [f for f in faithfulness if not f.grounded]
    if completeness or value_changes or any(f.severity == "high" for f in ungrounded):
        return "regenerate"
    if schema_issues or any(f.severity == "med" for f in ungrounded):
        return "review"
    return "pass"


def compute_score(faithfulness: list[Finding]) -> float:
    if not faithfulness:
        return 1.0
    grounded = sum(1 for f in faithfulness if f.grounded)
    return grounded / len(faithfulness)


def verify_grounding(doc: ExtractedDoc, structured_md: str, llm: LLMProvider) -> _GroundingOutput:
    system = GROUNDING_PROMPT_PATH.read_text(encoding="utf-8")
    original_text = render_blocks_naive(doc)
    user = f"ORIGINAL (from extracted source blocks):\n{original_text}\n\nGENERATED BODY:\n{structured_md}"
    result, _flags = llm.complete_structured(
        system=system,
        user=user,
        schema=_GroundingOutput,
        context={"faithfulness": [], "completeness": [], "value_changes": []},
    )
    return result


def curate_relations(
    fm: WikiFrontmatter,
    raw_relations: list[Relation],
    neighbor_doc_ids: list[str],
    llm: LLMProvider,
    relation_types: list[str],
) -> list[RelationSuggestion]:
    system = CURATION_PROMPT_PATH.read_text(encoding="utf-8")
    user = (
        f"Document: {fm.title} ({fm.id})\n"
        f"Current relations: {[r.model_dump() for r in raw_relations]}\n"
        f"Candidate neighbor doc_ids: {neighbor_doc_ids}\n"
        f"Allowed relation types: {relation_types}"
    )
    fallback_relations = [
        {
            "action": "keep",
            "type": r.type,
            "target": r.target,
            "confidence": 1.0,
            "rationale": "unchanged (fallback: no curation performed)",
        }
        for r in raw_relations
    ]
    result, _flags = llm.complete_structured(
        system=system,
        user=user,
        schema=_CurationOutput,
        context={"relations": fallback_relations},
    )
    return result.relations


def apply_curation(
    raw_relations: list[Relation],
    suggestions: list[RelationSuggestion],
    valid_target_ids: set[str],
    allowed_types: set[str],
) -> tuple[list[Relation], list[str]]:
    """prune removes a matching relation; add is only accepted if its type is
    allowed and its target is a known/candidate doc_id -- anything else is
    recorded as a schema issue instead of silently applied or raised.
    """
    issues: list[str] = []
    pruned = {(s.type, s.target) for s in suggestions if s.action == "prune"}
    kept = [r for r in raw_relations if (r.type, r.target) not in pruned]

    for suggestion in suggestions:
        if suggestion.action != "add":
            continue
        if suggestion.type not in allowed_types:
            issues.append(f"ignored add-suggestion with unknown relation type {suggestion.type!r}")
            continue
        if suggestion.target not in valid_target_ids:
            issues.append(f"ignored add-suggestion targeting unrecognized doc_id {suggestion.target!r}")
            continue
        relation = Relation(type=suggestion.type, target=suggestion.target)
        if relation not in kept:
            kept.append(relation)

    return kept, issues


def verify_and_curate(
    doc: ExtractedDoc,
    structured_md: str,
    enrichment: Enrichment,
    fm: WikiFrontmatter,
    raw_relations: list[Relation],
    llm: LLMProvider,
    relation_types: list[str],
    neighbor_doc_ids: list[str],
    attempt: int,
) -> VerificationReport:
    grounding = verify_grounding(doc, structured_md, llm)
    suggestions = curate_relations(fm, raw_relations, neighbor_doc_ids, llm, relation_types)

    valid_targets = set(neighbor_doc_ids) | {r.target for r in raw_relations}
    _, curation_issues = apply_curation(raw_relations, suggestions, valid_targets, set(relation_types))

    schema_issues = check_schema_issues(fm, enrichment) + curation_issues
    verdict = compute_verdict(
        grounding.faithfulness, grounding.completeness, grounding.value_changes, schema_issues
    )
    score = compute_score(grounding.faithfulness)

    return VerificationReport(
        doc_id=fm.id,
        verdict=verdict,
        score=score,
        attempt=attempt,
        faithfulness=grounding.faithfulness,
        completeness=grounding.completeness,
        value_changes=grounding.value_changes,
        schema_issues=schema_issues,
        relations=suggestions,
    )


def build_regen_hint(report: VerificationReport) -> str:
    parts: list[str] = []
    if report.completeness:
        parts.append("Missing from the previous attempt: " + "; ".join(report.completeness))
    if report.value_changes:
        vc_desc = "; ".join(
            f"{vc.kind} changed from {vc.original_value!r} to {vc.changed_value!r} "
            f"(should be {vc.original_value!r})"
            for vc in report.value_changes
        )
        parts.append("Values altered from the original that must be restored exactly: " + vc_desc)
    ungrounded = [f.claim for f in report.faithfulness if not f.grounded]
    if ungrounded:
        parts.append("Unsupported claims to fix or remove: " + "; ".join(ungrounded))
    return " ".join(parts)


def annotate_body(body: str, report: VerificationReport) -> str:
    """Prepends human-readable <!-- S5.5 ... --> comments summarizing the
    report, stripped again before chunking/embedding by
    builder/review.py::strip_review_comments. Returns `body` unchanged when
    there's nothing worth flagging (verdict pass, no add/prune actions) --
    this is what keeps Fake-mode/offline-test output byte-identical.
    """
    lines: list[str] = []
    if report.verdict != "pass":
        lines.append(f"<!-- S5.5 VERDICT: {report.verdict} (score={report.score:.2f}, attempt={report.attempt}) -->")
    for finding in report.faithfulness:
        if not finding.grounded:
            lines.append(f"<!-- S5.5 UNGROUNDED ({finding.severity}): {finding.claim} -->")
    for missing in report.completeness:
        lines.append(f"<!-- S5.5 MISSING: {missing} -->")
    for vc in report.value_changes:
        lines.append(f"<!-- S5.5 VALUE_CHANGED ({vc.kind}): {vc.original_value} -> {vc.changed_value} -->")
    for suggestion in report.relations:
        if suggestion.action in ("add", "prune"):
            lines.append(
                f"<!-- S5.5 RELATION {suggestion.action.upper()}: {suggestion.type} -> {suggestion.target} "
                f"(confidence={suggestion.confidence:.2f}) {suggestion.rationale} -->"
            )

    if not lines:
        return body
    return "\n".join(lines) + "\n\n" + body


def strip_previous_annotations(body: str) -> str:
    """Used by `wiki verify` so re-running it doesn't stack up duplicate
    S5.5 comment blocks from earlier runs -- REVIEW comments are untouched.
    """
    stripped = _S55_COMMENT_RE.sub("", body)
    if stripped == body:
        return body
    return stripped.strip() + "\n"


def plan_relink(
    fm: WikiFrontmatter,
    neighbor_doc_ids: list[str],
    llm: LLMProvider,
    relation_types: list[str],
) -> tuple[list[Relation], list[RelationSuggestion], list[str]]:
    """`wiki relink` (extension, additive): re-curate an already-approved
    document's relations against the current corpus (e.g. documents approved
    after it was). No grounding re-check and no regeneration -- this is a
    lighter, on-demand batch operation over 6-B only.
    """
    raw_relations = list(fm.relations)
    suggestions = curate_relations(fm, raw_relations, neighbor_doc_ids, llm, relation_types)
    valid_targets = set(neighbor_doc_ids) | {r.target for r in raw_relations}
    new_relations, issues = apply_curation(raw_relations, suggestions, valid_targets, set(relation_types))
    return new_relations, suggestions, issues
