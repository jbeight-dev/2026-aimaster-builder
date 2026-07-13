"""S2 - Structuring: reorganize S1 blocks into a normalized Markdown body.

A deterministic, non-LLM rendering of the blocks (`render_blocks_naive`) is
always computed first and passed as `context["markdown"]` to
LLMProvider.complete_structured(). FakeLLMProvider returns it verbatim (no
network); AzureLLMProvider only falls back to it if the real call fails twice
-- so the pipeline never crashes and always produces *something* traceable to
the source blocks.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from core.providers import LLMProvider
from core.schemas import Block, ExtractedDoc

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "structuring.md"


class StructuredBody(BaseModel):
    markdown: str
    review_flags: list[str] = Field(default_factory=list)


def render_table(block: Block) -> str:
    header = block.header or []
    rows = block.rows or []
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    table_md = "\n".join(lines)
    if block.caption:
        return f"{table_md}\n\n*{block.caption}*"
    return table_md


def render_blocks_naive(doc: ExtractedDoc) -> str:
    lines = [f"# {doc.title}", ""]
    for block in doc.blocks:
        if block.type == "heading":
            level = min((block.level or 2) + 1, 6)
            lines.append(f"{'#' * level} {block.text}")
            lines.append("")
        elif block.type == "paragraph":
            if block.text:
                lines.append(block.text)
                lines.append("")
        elif block.type == "table":
            lines.append(render_table(block))
            lines.append("")
        elif block.type == "code":
            lang = block.caption or ""
            lines.append(f"```{lang}")
            lines.append(block.text or "")
            lines.append("```")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def structure_document(doc: ExtractedDoc, llm: LLMProvider, regen_hint: str | None = None) -> tuple[str, list[str]]:
    """regen_hint (extension, additive): optional feedback from a S5.5
    verification failure (builder/verify_curate.py), fed back in on a
    regeneration attempt. None for every existing caller -- default behavior
    is unchanged.
    """
    system = PROMPT_PATH.read_text(encoding="utf-8")
    fallback_markdown = render_blocks_naive(doc)

    user = (
        f"Document title: {doc.title}\n\n"
        "Extracted blocks (JSON, in original order):\n"
        f"{[b.model_dump(exclude_none=True) for b in doc.blocks]}\n\n"
        "A naive baseline rendering (for reference, improve on this where useful, "
        "but never remove factual content from it):\n\n"
        f"{fallback_markdown}"
    )
    if regen_hint:
        user += f"\n\nThe previous attempt had issues, please fix them: {regen_hint}"

    result, flags = llm.complete_structured(
        system=system,
        user=user,
        schema=StructuredBody,
        context={"markdown": fallback_markdown},
    )
    return result.markdown, flags + result.review_flags
