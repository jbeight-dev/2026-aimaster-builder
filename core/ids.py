"""Deterministic ID helpers. No registry DB: identity is either derived from a
stable input (path, doc_id, section_path, chunk_idx) or reused by looking it up
in the in-memory WikiIndex built from approved md frontmatter.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

# \w is Unicode-aware for str patterns in Python 3 (matches Hangul, CJK, etc,
# not just [a-zA-Z0-9_]) -- restricting this to a-z0-9 silently strips
# non-Latin titles/filenames down to "", collapsing every Korean-only title to
# the same "doc" fallback and colliding distinct documents into one doc_id.
_SLUG_STRIP_RE = re.compile(r"[^\w\-]+", re.UNICODE)
_SLUG_DASH_COLLAPSE_RE = re.compile(r"-+")


def normalize_path(path: Path) -> str:
    return str(Path(path).resolve()).replace("\\", "/").lower()


def make_source_id(path: Path) -> str:
    """Path-based stable identity: re-ingesting the same path always yields the
    same source_id, regardless of content changes (content changes are tracked
    separately via a hash inside manifest.json -- see core/manifest.py).
    """
    stem = Path(path).stem
    digest = hashlib.sha1(normalize_path(path).encode("utf-8")).hexdigest()[:8]
    stem_slug = slugify(stem) or "source"
    return f"{stem_slug}-{digest}"


def content_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = text.replace("_", "-")
    text = _SLUG_STRIP_RE.sub("-", text)
    text = _SLUG_DASH_COLLAPSE_RE.sub("-", text).strip("-")
    return text or "doc"


def resolve_doc_id(
    source_id: str,
    title: str,
    by_source_id: dict[str, str],
    existing_doc_ids: set[str],
) -> tuple[str, str]:
    """Decision C: reuse the existing doc_id if this source_id has been seen
    before (avoids orphaning relations/vectors on re-ingest). Otherwise mint a
    new wiki-{slug} doc_id, appending -2/-3/... on collision.
    """
    existing = by_source_id.get(source_id)
    if existing:
        slug = existing.removeprefix("wiki-")
        return existing, slug

    base_slug = slugify(title)
    slug = base_slug
    doc_id = f"wiki-{slug}"
    n = 2
    while doc_id in existing_doc_ids:
        slug = f"{base_slug}-{n}"
        doc_id = f"wiki-{slug}"
        n += 1
    return doc_id, slug


def chunk_point_id(namespace: uuid.UUID, doc_id: str, section_path: str, chunk_idx: int) -> str:
    return str(uuid.uuid5(namespace, f"{doc_id}:{section_path}:{chunk_idx}"))


def summary_point_id(namespace: uuid.UUID, doc_id: str) -> str:
    return str(uuid.uuid5(namespace, f"{doc_id}:summary"))


def chunk_id(namespace: uuid.UUID, doc_id: str, section_path: str, chunk_idx: int) -> str:
    """Same value as chunk_point_id -- chunk_id IS the Qdrant point id (decision G)."""
    return chunk_point_id(namespace, doc_id, section_path, chunk_idx)
