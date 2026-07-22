"""S7 chunk_context assembly (decision A) + embedding. This is the ONLY place
chunk_context gets built -- from section_summaries produced back in S3 plus
the actual chunker output, never predicted ahead of time.
"""
from __future__ import annotations

import uuid

from core.ids import chunk_point_id
from core.progress import NULL_REPORTER, StageReporter
from core.providers import Embedder
from core.schemas import Chunk, RawChunk, SectionSummary

STEP_EMBED = "embed / 임베딩 생성"


def build_chunk_context(
    doc_title: str, doc_summary: str, section_summaries: list[SectionSummary], chunk: RawChunk
) -> str:
    match = next(
        (s.summary for s in section_summaries if s.section_path == chunk.section_path), None
    )
    summary = match if match is not None else doc_summary
    parts = [p for p in (doc_title, chunk.section_path, summary) if p]
    return " · ".join(parts)


def embed_chunks(
    doc_id: str,
    raw_chunks: list[RawChunk],
    doc_title: str,
    doc_summary: str,
    section_summaries: list[SectionSummary],
    embedder: Embedder,
    namespace: uuid.UUID,
    reporter: StageReporter = NULL_REPORTER,
) -> list[tuple[Chunk, list[float]]]:
    if not raw_chunks:
        return []

    reporter.start(STEP_EMBED, f"{len(raw_chunks)}개 청크 벡터화")
    contexts = [build_chunk_context(doc_title, doc_summary, section_summaries, c) for c in raw_chunks]
    embed_inputs = [f"{ctx}\n\n{c.text}" for ctx, c in zip(contexts, raw_chunks)]
    vectors = embedder.embed(embed_inputs)
    reporter.finish(STEP_EMBED, f"{len(raw_chunks)}개 청크 벡터화")
    reporter.log(STEP_EMBED, f"{len(raw_chunks)}개 청크 임베딩 완료 (dim={embedder.dimension})")

    result: list[tuple[Chunk, list[float]]] = []
    for raw_chunk, ctx, vector in zip(raw_chunks, contexts, vectors):
        chunk_id = chunk_point_id(namespace, doc_id, raw_chunk.section_path, raw_chunk.chunk_idx)
        chunk = Chunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            section_path=raw_chunk.section_path,
            chunk_idx=raw_chunk.chunk_idx,
            text=raw_chunk.text,
            source_page=raw_chunk.source_page,
            meta={"chunk_context": ctx},
        )
        result.append((chunk, vector))
    return result
