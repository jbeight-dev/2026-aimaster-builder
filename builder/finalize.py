"""S7-S8 - Indexing + Finalize. approve_document reads the draft from the
wiki_draft filesystem path (builder/review.py::read_draft); reindex_document
reads the already-approved doc straight off disk.

Postgres-backed draft storage (core/db.py) was tried and reverted: nothing in
this repo writes drafts into that table, so approve_document couldn't find
any draft ingest actually produces. Ingest stays filesystem-only for now.

section_summaries/doc_summary needed for chunk_context (decision A) live in
the S3 staging artifact, not in the frontmatter -- staging is never deleted
(guardrail), so it's always there to read back. The intake-level source_id
(needed to find that staging directory) is recovered from
`frontmatter.source.raw_path`'s parent directory name rather than stored
redundantly, since raw_path already encodes it uniformly for every format.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from builder import review as review_mod
from builder.indexing.chunker import chunk_markdown
from builder.indexing.embedder import embed_chunks
from builder.indexing.qdrant_writer import VectorStore, reindex_document as _reindex_points, upsert_document as _upsert_points
from core import wiki_io
from core.providers import Embedder
from core.schemas import Chunk, Enrichment, WikiFrontmatter


def _intake_source_id_of(fm: WikiFrontmatter) -> str:
    return Path(fm.source.raw_path).parent.name


def _load_enrichment(staging_root: Path, fm: WikiFrontmatter) -> Enrichment:
    path = Path(staging_root) / _intake_source_id_of(fm) / "03_enrichment" / f"{fm.source.source_id}.json"
    if not path.exists():
        raise RuntimeError(
            f"Missing S3 enrichment artifact at {path}. It's required to build chunk_context "
            "(decision A) and is only produced by `wiki ingest` -- re-ingest the source first."
        )
    return Enrichment.model_validate_json(path.read_text(encoding="utf-8"))


def _prepare_index_payload(
    fm: WikiFrontmatter,
    body: str,
    staging_root: Path,
    embedder: Embedder,
    namespace: uuid.UUID,
    chunking: dict[str, Any],
) -> tuple[list[float], list[tuple[Chunk, list[float]]]]:
    enrichment = _load_enrichment(staging_root, fm)
    clean_body = review_mod.strip_review_comments(body)
    raw_chunks = chunk_markdown(clean_body, chunking["max_tokens"], chunking.get("overlap_tokens", 0))
    chunks_with_vectors = embed_chunks(
        fm.id, raw_chunks, fm.title, enrichment.doc_summary, enrichment.section_summaries, embedder, namespace
    )
    summary_vector = embedder.embed([fm.summary])[0]
    return summary_vector, chunks_with_vectors


def approve_document(
    doc_id: str,
    paths: dict[str, Any],
    embedder: Embedder,
    vector_store: VectorStore,
    namespace: uuid.UUID,
    embed_model: str,
    chunking: dict[str, Any],
) -> WikiFrontmatter:
    fm, body = review_mod.read_draft(paths["wiki_draft"], doc_id)

    summary_vector, chunks_with_vectors = _prepare_index_payload(
        fm, body, paths["staging"], embedder, namespace, chunking
    )
    _upsert_points(vector_store, namespace, fm, summary_vector, chunks_with_vectors, embed_model, embedder.dimension)

    approved_fm = fm.model_copy(update={"review_status": "approved"})
    wiki_io.write(Path(paths["wiki_approved"]) / f"{doc_id}.md", approved_fm, body)
    review_mod.delete_draft(paths["wiki_draft"], doc_id)
    return approved_fm


def preview_reindex(doc_id: str, paths: dict[str, Any], chunking: dict[str, Any]) -> dict[str, Any]:
    """Dry-run: reports what a reindex would do without calling the embedder
    or touching the vector store (destructive-op guardrail).
    """
    fm, body = wiki_io.read(Path(paths["wiki_approved"]) / f"{doc_id}.md")
    clean_body = review_mod.strip_review_comments(body)
    raw_chunks = chunk_markdown(clean_body, chunking["max_tokens"], chunking.get("overlap_tokens", 0))
    return {
        "doc_id": doc_id,
        "would_delete_existing_points": True,
        "would_upsert_summary_points": 1,
        "would_upsert_chunk_points": len(raw_chunks),
    }


def reindex_document(
    doc_id: str,
    paths: dict[str, Any],
    embedder: Embedder,
    vector_store: VectorStore,
    namespace: uuid.UUID,
    embed_model: str,
    chunking: dict[str, Any],
) -> WikiFrontmatter:
    fm, body = wiki_io.read(Path(paths["wiki_approved"]) / f"{doc_id}.md")

    summary_vector, chunks_with_vectors = _prepare_index_payload(
        fm, body, paths["staging"], embedder, namespace, chunking
    )
    _reindex_points(vector_store, namespace, fm, summary_vector, chunks_with_vectors, embed_model, embedder.dimension)
    return fm
