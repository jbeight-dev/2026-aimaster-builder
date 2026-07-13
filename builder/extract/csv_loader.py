"""CSV -> a single markdown table block + a caption paragraph describing
columns/dtypes/row count. Per v2 spec decision: csv is NOT given a separate
tabular schema -- it converges on the same Block shape as every other format.
"""
from __future__ import annotations

from pathlib import Path

import chardet
import pandas as pd

from core.schemas import Block, ExtractedDoc

MAX_ROWS = 500  # PoC guard: full table if small, sampled if large (decision I)


class CsvLoader:
    def load(self, path: Path, source_id: str, title_hint: str) -> list[ExtractedDoc]:
        raw = Path(path).read_bytes()
        encoding = chardet.detect(raw).get("encoding") or "utf-8"
        df = pd.read_csv(path, encoding=encoding)

        total_rows = len(df)
        sampled = df.head(MAX_ROWS)
        truncated = total_rows > MAX_ROWS

        columns_desc = ", ".join(f"{c} ({df[c].dtype})" for c in df.columns)
        caption_text = (
            f"CSV dataset with {total_rows} rows and {len(df.columns)} columns: {columns_desc}."
        )
        if truncated:
            caption_text += f" Showing first {MAX_ROWS} rows (large table, see decision I / README)."

        blocks = [
            Block(type="heading", level=1, text=title_hint),
            Block(type="paragraph", text=caption_text),
            Block(
                type="table",
                header=[str(c) for c in df.columns],
                rows=sampled.astype(str).values.tolist(),
                caption=title_hint,
            ),
        ]

        return [
            ExtractedDoc(
                doc_id=source_id,
                source_id=source_id,
                source_type="csv",
                title=title_hint,
                blocks=blocks,
            )
        ]
