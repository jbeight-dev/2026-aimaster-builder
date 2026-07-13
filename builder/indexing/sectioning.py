"""Shared Markdown heading-sectioning used by BOTH S3 (naive section_summaries
fallback) and S7 (the real chunker + chunk_context assembly). Keeping this in
one place guarantees `section_path` is computed the same way everywhere, which
is what lets S7's chunk_context lookup (builder/indexing/embedder.py) match a
chunk's section_path back to a section_summary produced back in S3.

section_path convention: '>'-joined heading text stack, EXCLUDING the level-1
document title (e.g. 'procedure>sensor init'). A document with no sub-headings
falls back to using the H1 title itself as the (sole) section's path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Section:
    path: str
    level: int
    heading_text: str
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


def _stack_path(stack: list[tuple[int, str]]) -> str:
    non_root = [text for lvl, text in stack if lvl > 1]
    if non_root:
        return ">".join(non_root)
    return stack[-1][1] if stack else ""


def parse_sections(markdown: str) -> list[Section]:
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    current: Section | None = None

    for line in markdown.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            current = Section(path=_stack_path(stack), level=level, heading_text=text)
            sections.append(current)
        else:
            if current is None:
                current = Section(path="", level=0, heading_text="")
                sections.append(current)
            current.lines.append(line)

    return sections
