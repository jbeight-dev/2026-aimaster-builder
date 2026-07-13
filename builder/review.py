"""S6 - Review (HITL). Decision B: no live interrupted graph. `wiki ingest`
writes wiki/draft/{doc_id}.md and the run ends; `wiki review list` scans that
directory; a separate `wiki approve <doc_id>` (S7-S8) reads the draft back and
resumes. Any accumulated review_flags from earlier stages (S2 structuring,
S3 enrichment degrade paths) are surfaced as `<!-- REVIEW: ... -->` comments
at the top of the body -- the single channel a human reviewer actually reads.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core import wiki_io
from core.schemas import WikiFrontmatter

REVIEW_COMMENT_RE = re.compile(r"<!--\s*REVIEW:.*?-->", re.DOTALL)

# S5.5 (extension, additive): verify_curate.annotate_body() prepends
# <!-- S5.5 ... --> comments (verdict/findings/relation actions). They must
# be stripped before chunking/embedding same as REVIEW comments, but are
# intentionally NOT counted by count_review_flags (a different signal from
# the human-facing REVIEW-flag count `wiki review list` already shows).
_ANNOTATION_COMMENT_RE = re.compile(r"<!--\s*(?:REVIEW|S5\.5[^:]*):.*?-->", re.DOTALL)


def append_review_flags(body: str, flags: list[str]) -> str:
    if not flags:
        return body
    comments = "\n".join(f"<!-- REVIEW: {flag} -->" for flag in flags)
    return f"{comments}\n\n{body}"


def count_review_flags(body: str) -> int:
    return len(REVIEW_COMMENT_RE.findall(body))


def strip_review_comments(body: str) -> str:
    """Used before chunking/embedding (S7) so reviewer-facing meta-comments
    (REVIEW flags and S5.5 verification/relation annotations) never leak into
    search content; the draft/approved md on disk keeps them.
    """
    return _ANNOTATION_COMMENT_RE.sub("", body).strip() + "\n"


def draft_path(draft_root: Path, doc_id: str) -> Path:
    return Path(draft_root) / f"{doc_id}.md"


def existing_draft_doc_ids(draft_root: Path) -> set[str]:
    """Pending drafts aren't in the approved WikiIndex yet, so doc_id collision
    checks (core/ids.py::resolve_doc_id) must also consult this set -- two
    still-unapproved documents that slugify to the same doc_id would otherwise
    silently overwrite each other's draft file (see review.py module docs).
    """
    draft_root = Path(draft_root)
    if not draft_root.exists():
        return set()
    return {p.stem for p in draft_root.glob("*.md")}


def write_draft(draft_root: Path, fm: WikiFrontmatter, body: str) -> Path:
    fm = fm.model_copy(update={"review_status": "draft"})
    path = draft_path(draft_root, fm.id)
    if path.exists():
        existing_fm, _ = wiki_io.read(path)
        if existing_fm.source.source_id != fm.source.source_id:
            raise ValueError(
                f"Refusing to overwrite draft {fm.id!r}: it belongs to source "
                f"{existing_fm.source.source_id!r}, not {fm.source.source_id!r}. "
                "This means resolve_doc_id's collision check missed an existing "
                "draft -- pass existing_draft_doc_ids() into it."
            )
    return wiki_io.write(path, fm, body)


def read_draft(draft_root: Path, doc_id: str) -> tuple[WikiFrontmatter, str]:
    return wiki_io.read(draft_path(draft_root, doc_id))


def delete_draft(draft_root: Path, doc_id: str) -> None:
    path = draft_path(draft_root, doc_id)
    if path.exists():
        path.unlink()


@dataclass
class DraftSummary:
    doc_id: str
    title: str
    version: int
    review_flag_count: int


def list_drafts(draft_root: Path) -> list[DraftSummary]:
    draft_root = Path(draft_root)
    if not draft_root.exists():
        return []
    summaries = []
    for path in sorted(draft_root.glob("*.md")):
        fm, body = wiki_io.read(path)
        summaries.append(
            DraftSummary(
                doc_id=fm.id,
                title=fm.title,
                version=fm.version,
                review_flag_count=count_review_flags(body),
            )
        )
    return summaries
