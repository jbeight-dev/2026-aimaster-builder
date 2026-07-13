"""S0 - Intake: validate the source, assign a stable source_id, copy the
original into raw/, record provenance, and load/init the manifest.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from core import manifest as manifest_io
from core.ids import content_hash, make_source_id
from core.schemas import SourceType

EXTENSION_MAP: dict[str, SourceType] = {
    ".pdf": "pdf",
    ".csv": "csv",
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
    ".sqlite": "sqlite",
    ".sqlite3": "sqlite",
    ".db": "sqlite",
}


class IntakeResult(BaseModel):
    source_id: str
    source_type: SourceType
    raw_path: str
    original_path: str
    content_hash: str
    changed: bool  # True if content_hash differs from the prior manifest's


def detect_source_type(path: Path) -> SourceType:
    ext = path.suffix.lower()
    if ext not in EXTENSION_MAP:
        raise ValueError(f"Unsupported source extension: {ext}")
    return EXTENSION_MAP[ext]


def run_intake(path: Path, raw_root: Path, staging_root: Path) -> IntakeResult:
    path = Path(path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Source file not found: {path}")

    source_type = detect_source_type(path)
    source_id = make_source_id(path)
    data = path.read_bytes()
    new_hash = content_hash(data)

    manifest = manifest_io.load_or_init(staging_root, source_id, str(path.resolve()))
    changed = manifest.content_hash != new_hash
    if changed:
        manifest_io.reset_from(manifest, "extract")
    manifest.content_hash = new_hash

    raw_dir = Path(raw_root) / source_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"original{path.suffix.lower()}"
    raw_path.write_bytes(data)

    source_meta = {
        "source_id": source_id,
        "source_type": source_type,
        "original_filename": path.name,
        "original_path": str(path.resolve()),
        "hash": new_hash,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    (raw_dir / "source_meta.json").write_text(
        json.dumps(source_meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    manifest_io.mark(manifest, "intake", "done")
    manifest_io.save(staging_root, manifest)

    return IntakeResult(
        source_id=source_id,
        source_type=source_type,
        raw_path=str(raw_path),
        original_path=str(path.resolve()),
        content_hash=new_hash,
        changed=changed,
    )
