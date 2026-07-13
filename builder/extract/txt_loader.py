"""Plain text -> blocks. chardet for encoding, blank-line paragraph splitting,
and a light heading heuristic (short line, no trailing punctuation, followed by
a blank line) since txt has no structural markup to lean on.
"""
from __future__ import annotations

from pathlib import Path

import chardet

from core.schemas import Block, ExtractedDoc

_HEADING_MAX_LEN = 60
_SENTENCE_ENDINGS = (".", "!", "?", ",", ";", ":")


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > _HEADING_MAX_LEN:
        return False
    if stripped.endswith(_SENTENCE_ENDINGS):
        return False
    return True


class TxtLoader:
    def load(self, path: Path, source_id: str, title_hint: str) -> list[ExtractedDoc]:
        raw = Path(path).read_bytes()
        encoding = chardet.detect(raw).get("encoding") or "utf-8"
        text = raw.decode(encoding, errors="replace")

        lines = text.splitlines()
        blocks: list[Block] = []
        paragraph_buf: list[str] = []

        def flush_paragraph() -> None:
            if paragraph_buf:
                joined = " ".join(paragraph_buf).strip()
                if joined:
                    blocks.append(Block(type="paragraph", text=joined))
                paragraph_buf.clear()

        for i, line in enumerate(lines):
            if not line.strip():
                flush_paragraph()
                continue
            next_blank = (i + 1 >= len(lines)) or (not lines[i + 1].strip())
            if _looks_like_heading(line) and next_blank and not paragraph_buf:
                flush_paragraph()
                blocks.append(Block(type="heading", level=1, text=line.strip()))
            else:
                paragraph_buf.append(line.strip())
        flush_paragraph()

        title = title_hint
        for b in blocks:
            if b.type == "heading":
                title = b.text or title
                break

        return [
            ExtractedDoc(
                doc_id=source_id,
                source_id=source_id,
                source_type="txt",
                title=title,
                blocks=blocks,
            )
        ]
