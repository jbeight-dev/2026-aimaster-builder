"""PDF -> blocks. Text/headings via font-size heuristic (PyMuPDF), tables via
pdfplumber. Scanned/OCR PDFs are out of scope for this PoC (spec marks OCR
optional; no sample requires it) -- pages with no extractable text are simply
skipped rather than producing empty blocks.
"""
from __future__ import annotations

from pathlib import Path
from statistics import median

import fitz  # PyMuPDF
import pdfplumber

from core.schemas import Block, ExtractedDoc


class PdfLoader:
    def load(self, path: Path, source_id: str, title_hint: str) -> list[ExtractedDoc]:
        blocks: list[Block] = []
        body_size = self._median_font_size(path)

        with fitz.open(path) as doc:
            for page_index, page in enumerate(doc, start=1):
                blocks.extend(self._page_text_blocks(page, page_index, body_size))

        blocks.extend(self._table_blocks(path))

        title = title_hint
        for b in blocks:
            if b.type == "heading":
                title = b.text or title
                break

        return [
            ExtractedDoc(
                doc_id=source_id,
                source_id=source_id,
                source_type="pdf",
                title=title,
                blocks=blocks,
            )
        ]

    def _median_font_size(self, path: Path) -> float:
        sizes: list[float] = []
        with fitz.open(path) as doc:
            for page in doc:
                for block in page.get_text("dict")["blocks"]:
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            if span["text"].strip():
                                sizes.append(span["size"])
        return median(sizes) if sizes else 10.0

    def _page_text_blocks(self, page, page_index: int, body_size: float) -> list[Block]:
        out: list[Block] = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                max_size = max(s["size"] for s in spans)
                if max_size >= body_size * 1.25:
                    level = 1 if max_size >= body_size * 1.5 else 2
                    out.append(Block(type="heading", level=level, text=text, page=page_index))
                else:
                    out.append(Block(type="paragraph", text=text, page=page_index))
        return out

    def _table_blocks(self, path: Path) -> list[Block]:
        out: list[Block] = []
        with pdfplumber.open(path) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                for table in page.extract_tables():
                    if not table or not table[0]:
                        continue
                    header = [c or "" for c in table[0]]
                    rows = [[c or "" for c in row] for row in table[1:]]
                    out.append(
                        Block(
                            type="table",
                            header=header,
                            rows=rows,
                            caption=f"Table on page {page_index}",
                            page=page_index,
                        )
                    )
        return out
