"""S2.5 - Conditional Translation: translate a normalized Wiki body into
Korean when it is written in English, so downstream stages (S3 enrichment,
S5.5 grounding, S6 draft) operate on a wiki that stays consistently Korean.

Language detection is a deterministic heuristic (no new dependency, no LLM
call for the decision itself), matching this codebase's preference for
deterministic judgment over LLM self-reporting (see builder/verify_curate.py
decision V6). Non-English documents skip the LLM call entirely.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.providers import LLMProvider

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "translate.md"

_HANGUL_RE = re.compile(r"[가-힣]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_MIN_LATIN_CHARS = 20
_MAX_HANGUL_RATIO = 0.2


def is_english(markdown: str) -> bool:
    """True if `markdown` (excluding fenced/inline code, which is never
    translated anyway) is predominantly Latin script: at least
    `_MIN_LATIN_CHARS` Latin letters, and Hangul at most `_MAX_HANGUL_RATIO`
    of (Hangul + Latin) letters.

    A ratio, not "any Hangul disqualifies it": prompts/structuring.md (S2)
    itself asks the model for Korean/English section headings like "## 개요"
    even on English source documents, so a handful of Korean headings/words
    must not stop an otherwise-English body from being translated.
    """
    text = _INLINE_CODE_RE.sub("", _CODE_FENCE_RE.sub("", markdown))
    latin = len(_LATIN_RE.findall(text))
    if latin < _MIN_LATIN_CHARS:
        return False
    hangul = len(_HANGUL_RE.findall(text))
    return hangul / (hangul + latin) <= _MAX_HANGUL_RATIO


def translate_document(structured_md: str, llm: LLMProvider) -> tuple[str, list[str]]:
    """Translates an English structured body into Korean. Non-English input
    is returned unchanged with no LLM call. FakeLLMProvider.complete() echoes
    `user` back verbatim, so this is a no-op under offline/Fake-mode tests
    regardless of language, same safety net as every other pipeline stage.
    """
    if not is_english(structured_md):
        return structured_md, []

    system = PROMPT_PATH.read_text(encoding="utf-8")
    translated = llm.complete(system=system, user=structured_md)
    flags = ["translated_en_to_ko"] if translated != structured_md else []
    return translated, flags
