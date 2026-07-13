"""S3 - Semantic Enrichment. Produces doc_summary/section_summaries/entities/
keywords/concepts (NO chunk_context -- see core/schemas.py docstring / decision
A). Entities are emitted WITHOUT canonical resolution here; that happens in
S4/S5 (builder/metadata.py, builder/relations.py) against the in-memory
WikiIndex, per decision D.
"""
from __future__ import annotations

from pathlib import Path

from core.providers import LLMProvider
from core.schemas import Enrichment
from builder.indexing.sectioning import parse_sections

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "enrichment.md"
_MAX_SUMMARY_CHARS = 240


def naive_doc_summary(markdown: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("|"):
            return stripped[:_MAX_SUMMARY_CHARS]
    return ""


def naive_section_summaries(markdown: str) -> list[dict]:
    out: list[dict] = []
    for section in parse_sections(markdown):
        if not section.path:
            continue
        text = " ".join(l.strip() for l in section.lines if l.strip() and not l.strip().startswith("|"))
        if not text:
            continue
        out.append({"section_path": section.path, "summary": text[:_MAX_SUMMARY_CHARS]})
    return out


def enrich_document(
    doc_id: str,
    structured_md: str,
    llm: LLMProvider,
    entity_types: list[str],
    regen_hint: str | None = None,
) -> Enrichment:
    """regen_hint (extension, additive): optional feedback from a S5.5
    verification failure (builder/verify_curate.py), fed back in on a
    regeneration attempt. None for every existing caller -- default behavior
    is unchanged.
    """
    system = PROMPT_PATH.read_text(encoding="utf-8")
    fallback = {
        "doc_id": doc_id,
        "doc_summary": naive_doc_summary(structured_md),
        "section_summaries": naive_section_summaries(structured_md),
        "entities": [],
        "keywords": [],
        "concepts": [],
    }

    user = (
        f"Allowed entity types: {entity_types}\n\n"
        f"Document body:\n{structured_md}"
    )
    if regen_hint:
        user += f"\n\nThe previous attempt had issues, please fix them: {regen_hint}"

    result, flags = llm.complete_structured(
        system=system, user=user, schema=Enrichment, context=fallback
    )
    if flags:
        result = result.model_copy(update={"review_flags": [*result.review_flags, *flags]})
    return result
