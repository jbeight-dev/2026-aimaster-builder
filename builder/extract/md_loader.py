"""Markdown -> blocks via markdown-it-py AST for headings/paragraphs/code
fences. GFM pipe tables aren't in the CommonMark token set markdown-it-py
ships without extra plugins, so table regions are pre-extracted with a small
line-based scanner and spliced back in as table blocks in original order.
"""
from __future__ import annotations

import re
from pathlib import Path

from markdown_it import MarkdownIt

from core.schemas import Block, ExtractedDoc

# CommonMark mandates replacing literal NUL bytes with U+FFFD during
# preprocessing, so the placeholder must avoid \x00 or markdown-it-py will
# never hand it back to us intact.
_TABLE_PLACEHOLDER = "TABLEPLACEHOLDER{i}"
_TABLE_PLACEHOLDER_RE = re.compile(r"TABLEPLACEHOLDER(\d+)")
_SEPARATOR_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _extract_tables(text: str) -> tuple[str, list[Block]]:
    lines = text.splitlines()
    out_lines: list[str] = []
    tables: list[Block] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        is_header_candidate = "|" in line and line.strip()
        has_separator = (
            is_header_candidate
            and i + 1 < len(lines)
            and "|" in lines[i + 1]
            and "-" in lines[i + 1]
            and _SEPARATOR_RE.match(lines[i + 1])
        )
        if has_separator:
            header = _split_row(line)
            j = i + 2
            rows: list[list[str]] = []
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                rows.append(_split_row(lines[j]))
                j += 1
            idx = len(tables)
            tables.append(Block(type="table", header=header, rows=rows))
            out_lines.append("")
            out_lines.append(_TABLE_PLACEHOLDER.format(i=idx))
            out_lines.append("")
            i = j
        else:
            out_lines.append(line)
            i += 1
    return "\n".join(out_lines), tables


class MdLoader:
    def load(self, path: Path, source_id: str, title_hint: str) -> list[ExtractedDoc]:
        raw_text = Path(path).read_text(encoding="utf-8", errors="replace")
        text, tables = _extract_tables(raw_text)

        md = MarkdownIt("commonmark")
        tokens = md.parse(text)

        blocks: list[Block] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.type == "heading_open":
                inline = tokens[i + 1]
                level = int(tok.tag[1])
                blocks.append(Block(type="heading", level=level, text=inline.content.strip()))
                i += 3
            elif tok.type == "paragraph_open":
                inline = tokens[i + 1]
                content = inline.content.strip()
                m = _TABLE_PLACEHOLDER_RE.fullmatch(content)
                if m:
                    blocks.append(tables[int(m.group(1))])
                elif content:
                    blocks.append(Block(type="paragraph", text=content))
                i += 3
            elif tok.type == "fence":
                blocks.append(Block(type="code", text=tok.content.rstrip("\n"), caption=tok.info.strip() or None))
                i += 1
            else:
                i += 1

        title = title_hint
        for b in blocks:
            if b.type == "heading":
                title = b.text or title
                break

        return [
            ExtractedDoc(
                doc_id=source_id,
                source_id=source_id,
                source_type="md",
                title=title,
                blocks=blocks,
            )
        ]
