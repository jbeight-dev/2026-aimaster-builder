"""Per-source step-completion tracking (decision E). Idempotency/resume is
judged from manifest.json, never from "does the raw dir exist" -- a failed run
resumes from the first incomplete step, and an unchanged re-ingest is a no-op.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

STEP_ORDER = [
    "intake",
    "extract",
    "structure",
    "translate",
    "enrich",
    "metadata",
    "relations",
    "draft",
]

StepName = Literal[
    "intake", "extract", "structure", "translate", "enrich", "metadata", "relations", "draft", "index"
]


class StepStatus(BaseModel):
    status: Literal["pending", "done", "failed"] = "pending"
    ts: datetime | None = None
    detail: str | None = None


class Manifest(BaseModel):
    source_id: str
    source_path: str
    content_hash: str | None = None
    doc_ids: list[str] = Field(default_factory=list)
    steps: dict[str, StepStatus] = Field(default_factory=dict)


def manifest_path(staging_root: Path, source_id: str) -> Path:
    return Path(staging_root) / source_id / "manifest.json"


def load_or_init(staging_root: Path, source_id: str, source_path: str) -> Manifest:
    path = manifest_path(staging_root, source_id)
    if path.exists():
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    return Manifest(source_id=source_id, source_path=source_path)


def save(staging_root: Path, manifest: Manifest) -> Path:
    path = manifest_path(staging_root, manifest.source_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return path


def mark(manifest: Manifest, step: str, status: str = "done", detail: str | None = None) -> None:
    manifest.steps[step] = StepStatus(status=status, ts=datetime.now(timezone.utc), detail=detail)


def is_done(manifest: Manifest, step: str) -> bool:
    st = manifest.steps.get(step)
    return bool(st and st.status == "done")


def resume_step(manifest: Manifest) -> str | None:
    """First step in STEP_ORDER that is not done. None means fully processed
    through 'draft' (indexing is tracked separately by the approve/reindex flow).
    """
    for step in STEP_ORDER:
        if not is_done(manifest, step):
            return step
    return None


def reset_from(manifest: Manifest, step: str) -> None:
    """Mark `step` and everything after it as pending again (used when content
    changes and processing must resume from 'structure' onward, decision C/E).
    """
    idx = STEP_ORDER.index(step)
    for s in STEP_ORDER[idx:]:
        manifest.steps.pop(s, None)
