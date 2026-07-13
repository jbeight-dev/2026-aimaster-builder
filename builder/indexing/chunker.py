"""Deterministic chunking, no LLM involved. Chunk boundaries are computed
purely from the approved body text + config thresholds, so re-chunking the
same body always yields the same (section_path, chunk_idx) pairs -- which is
what makes the uuid5 point IDs (core/ids.py, decision G) stable across runs.

Word count is used as a token proxy (no tokenizer dependency) -- adequate for
a PoC chunk-size guard, not an exact token budget.
"""
from __future__ import annotations

from core.schemas import RawChunk
from builder.indexing.sectioning import parse_sections

_INTRO_SECTION_PATH = "intro"


def chunk_markdown(body: str, max_tokens: int, overlap_tokens: int = 0) -> list[RawChunk]:
    chunks: list[RawChunk] = []
    step = max(max_tokens - overlap_tokens, 1)

    for section in parse_sections(body):
        text = section.text
        if not text:
            continue
        section_path = section.path or _INTRO_SECTION_PATH
        words = text.split()

        idx = 0
        chunk_idx = 0
        while idx < len(words):
            window = words[idx : idx + max_tokens]
            chunks.append(
                RawChunk(
                    section_path=section_path,
                    chunk_idx=chunk_idx,
                    text=" ".join(window),
                    source_page=None,
                )
            )
            chunk_idx += 1
            idx += step

    return chunks
