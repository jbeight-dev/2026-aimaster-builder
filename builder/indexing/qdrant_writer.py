"""S7 - Indexing. VectorStore interface (architectural invariant #4) plus two
Qdrant implementations that share all collection/point logic and differ only
in how the underlying qdrant-client is connected: `QdrantLocalStore`
(embedded, `path=`, no server -- what the offline demo/tests use) and
`QdrantCloudStore` (`url=` + `api_key=`, a real Qdrant server/Cloud
instance -- see core/factory.py::build_vector_store for how `qdrant.mode` in
config/settings.yaml, overridable via QDRANT_MODE, picks between them).
`embed_model` is stamped into every payload (decision F); `review_status` is
omitted (only approved docs ever get indexed). Point IDs are decided upstream
(core/ids.py, decision G) -- this module just persists whatever it's given.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from core.ids import summary_point_id
from core.schemas import Chunk, WikiFrontmatter


class VectorStore(ABC):
    @abstractmethod
    def ensure_collections(self, dim: int) -> None: ...

    @abstractmethod
    def upsert_summary(self, point_id: str, vector: list[float], payload: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_chunks(self, points: list[tuple[str, list[float], dict[str, Any]]]) -> None: ...

    @abstractmethod
    def delete_by_doc_id(self, doc_id: str) -> None: ...

    @abstractmethod
    def counts(self) -> dict[str, int]: ...


class _QdrantStore(VectorStore):
    """Collection/point operations shared by every Qdrant connection mode --
    everything here only ever touches `self.client`, so local vs. cloud is
    entirely decided by how a subclass constructs that client.
    """

    def __init__(
        self,
        client: Any,
        namespace: uuid.UUID,
        collection_summary: str = "wiki_summary",
        collection_chunk: str = "wiki_chunk",
    ):
        self.client = client
        self.namespace = namespace
        self.collection_summary = collection_summary
        self.collection_chunk = collection_chunk

    def ensure_collections(self, dim: int) -> None:
        from qdrant_client.models import Distance, VectorParams

        for name in (self.collection_summary, self.collection_chunk):
            if not self.client.collection_exists(name):
                self.client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                )

    def upsert_summary(self, point_id: str, vector: list[float], payload: dict[str, Any]) -> None:
        from qdrant_client.models import PointStruct

        self.client.upsert(
            self.collection_summary, points=[PointStruct(id=point_id, vector=vector, payload=payload)]
        )

    def upsert_chunks(self, points: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        from qdrant_client.models import PointStruct

        if not points:
            return
        structs = [PointStruct(id=pid, vector=vec, payload=payload) for pid, vec, payload in points]
        self.client.upsert(self.collection_chunk, points=structs)

    def delete_by_doc_id(self, doc_id: str) -> None:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

        flt = Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])
        for name in (self.collection_summary, self.collection_chunk):
            if self.client.collection_exists(name):
                self.client.delete(name, points_selector=FilterSelector(filter=flt))

    def counts(self) -> dict[str, int]:
        out = {}
        for name in (self.collection_summary, self.collection_chunk):
            out[name] = self.client.count(name).count if self.client.collection_exists(name) else 0
        return out


class QdrantLocalStore(_QdrantStore):
    """Embedded mode (`qdrant.mode: local`): qdrant-client owns an on-disk
    storage directory directly, no server involved. Single-process only --
    see api/deps.py's module docstring for why the API caches one instance
    per process instead of building a fresh one per request.
    """

    def __init__(
        self,
        path: Path,
        namespace: uuid.UUID,
        collection_summary: str = "wiki_summary",
        collection_chunk: str = "wiki_chunk",
    ):
        from qdrant_client import QdrantClient

        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        super().__init__(QdrantClient(path=str(self.path)), namespace, collection_summary, collection_chunk)


class QdrantCloudStore(_QdrantStore):
    """Remote mode (`qdrant.mode: cloud`): talks to a real Qdrant server or
    Qdrant Cloud cluster over HTTP, so (unlike QdrantLocalStore) it's safe to
    have multiple concurrent instances/processes pointed at it.
    """

    def __init__(
        self,
        url: str,
        api_key: str,
        namespace: uuid.UUID,
        collection_summary: str = "wiki_summary",
        collection_chunk: str = "wiki_chunk",
    ):
        from qdrant_client import QdrantClient

        super().__init__(
            QdrantClient(url=url, api_key=api_key), namespace, collection_summary, collection_chunk
        )


def summary_payload(fm: WikiFrontmatter, embed_model: str) -> dict[str, Any]:
    return {
        "doc_id": fm.id,
        "title": fm.title,
        "doc_type": fm.doc_type,
        "tags": fm.tags,
        "embed_model": embed_model,
    }


def chunk_payload(fm: WikiFrontmatter, chunk: Chunk, embed_model: str) -> dict[str, Any]:
    return {
        "doc_id": fm.id,
        "section_path": chunk.section_path,
        "chunk_idx": chunk.chunk_idx,
        "source_id": fm.source.source_id,
        "source_page": chunk.source_page,
        "doc_type": fm.doc_type,
        "tags": fm.tags,
        "version": fm.version,
        "embed_model": embed_model,
    }


def upsert_document(
    store: VectorStore,
    namespace: uuid.UUID,
    fm: WikiFrontmatter,
    summary_vector: list[float],
    chunks_with_vectors: list[tuple[Chunk, list[float]]],
    embed_model: str,
    dim: int,
) -> None:
    """The single upsert code path used by both first-time approve and
    reindex (decision G): callers that need reindex semantics call
    delete_by_doc_id() first, then this.
    """
    store.ensure_collections(dim)
    store.upsert_summary(summary_point_id(namespace, fm.id), summary_vector, summary_payload(fm, embed_model))
    store.upsert_chunks(
        [(chunk.chunk_id, vector, chunk_payload(fm, chunk, embed_model)) for chunk, vector in chunks_with_vectors]
    )


def reindex_document(
    store: VectorStore,
    namespace: uuid.UUID,
    fm: WikiFrontmatter,
    summary_vector: list[float],
    chunks_with_vectors: list[tuple[Chunk, list[float]]],
    embed_model: str,
    dim: int,
) -> None:
    store.delete_by_doc_id(fm.id)
    upsert_document(store, namespace, fm, summary_vector, chunks_with_vectors, embed_model, dim)
