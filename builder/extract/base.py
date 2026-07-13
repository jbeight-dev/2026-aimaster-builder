"""S1 - Extraction: format-specific loaders converge on the shared
ExtractedDoc/Block schema (core/schemas.py). Every loader implements the same
Protocol so structuring.py (S2) never needs to know the source format.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from core.schemas import ExtractedDoc, ExtractionResult, SourceType


class BaseLoader(Protocol):
    def load(self, path: Path, source_id: str, title_hint: str) -> list[ExtractedDoc]: ...


def extracted_path(staging_root: Path, source_id: str) -> Path:
    return Path(staging_root) / source_id / "01_extracted.json"


def get_loader(source_type: SourceType) -> BaseLoader:
    # Local imports avoid pulling in every loader's dependency (fitz, pandas, ...)
    # unless that format is actually used.
    if source_type == "pdf":
        from builder.extract.pdf_loader import PdfLoader

        return PdfLoader()
    if source_type == "csv":
        from builder.extract.csv_loader import CsvLoader

        return CsvLoader()
    if source_type == "txt":
        from builder.extract.txt_loader import TxtLoader

        return TxtLoader()
    if source_type == "md":
        from builder.extract.md_loader import MdLoader

        return MdLoader()
    if source_type == "sqlite":
        from builder.extract.sqlite_loader import SqliteLoader

        return SqliteLoader()
    raise ValueError(f"No loader for source_type={source_type!r}")


def run_extraction(
    raw_path: Path, source_id: str, source_type: SourceType, staging_root: Path, title_hint: str
) -> ExtractionResult:
    """`title_hint` is the originally uploaded filename's stem (e.g. 'dataset'
    for dataset.csv). It's passed in explicitly because `raw_path` always
    points at the renamed raw copy (raw/{source_id}/original.{ext}), which
    would otherwise make every title-less document (no heading to derive a
    title from, e.g. csv) come out literally titled 'original'.
    """
    loader = get_loader(source_type)
    documents = loader.load(Path(raw_path), source_id, title_hint)
    result = ExtractionResult(source_id=source_id, source_type=source_type, documents=documents)

    out_path = extracted_path(staging_root, source_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result
