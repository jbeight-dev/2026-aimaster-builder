"""md <-> (frontmatter, body) (de)serialization, plus the in-memory WikiIndex
(decision D): approved/*.md frontmatter is the only registry there is. Loaded
once at boot, queried bidirectionally, mutated in-process during a run, and
never written back except by S6/S8 rewriting the actual md files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.schemas import Relation, WikiFrontmatter

FRONTMATTER_DELIM = "---"


def serialize(fm: WikiFrontmatter, body: str) -> str:
    data = fm.model_dump(mode="json", exclude_none=True)
    yaml_text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return f"{FRONTMATTER_DELIM}\n{yaml_text}{FRONTMATTER_DELIM}\n\n{body.strip()}\n"


def parse(md_text: str) -> tuple[WikiFrontmatter, str]:
    if not md_text.startswith(FRONTMATTER_DELIM):
        raise ValueError("Wiki markdown is missing a leading frontmatter delimiter")
    parts = md_text.split(FRONTMATTER_DELIM, 2)
    if len(parts) < 3:
        raise ValueError("Malformed frontmatter: expected two '---' delimiters")
    yaml_text, body = parts[1], parts[2]
    data = yaml.safe_load(yaml_text) or {}
    fm = WikiFrontmatter.model_validate(data)
    return fm, body.lstrip("\n")


def read(path: Path) -> tuple[WikiFrontmatter, str]:
    return parse(Path(path).read_text(encoding="utf-8"))


def write(path: Path, fm: WikiFrontmatter, body: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize(fm, body), encoding="utf-8")
    return path


@dataclass
class WikiIndex:
    docs: dict[str, WikiFrontmatter] = field(default_factory=dict)
    by_source_id: dict[str, str] = field(default_factory=dict)
    entity_canonicals: dict[str, str] = field(default_factory=dict)
    relations_fwd: dict[str, list[Relation]] = field(default_factory=dict)
    relations_rev: dict[str, list[tuple[str, Relation]]] = field(default_factory=dict)
    unresolved_entities: list[dict] = field(default_factory=list)

    def resolve_entity(self, name: str, aliases: list[str] | None = None) -> str | None:
        for candidate in [name, *(aliases or [])]:
            hit = self.entity_canonicals.get(candidate.strip().lower())
            if hit:
                return hit
        return None

    def register_entity(self, name: str, canonical: str, aliases: list[str] | None = None) -> None:
        for candidate in [name, *(aliases or [])]:
            key = candidate.strip().lower()
            if key:
                self.entity_canonicals.setdefault(key, canonical)

    def add_document(self, fm: WikiFrontmatter) -> None:
        """Registers a document's frontmatter into the index (both directions
        of its relations, and first-seen-wins entity canonicals).
        """
        self.docs[fm.id] = fm
        self.by_source_id[fm.source.source_id] = fm.id
        for entity in fm.entities:
            canonical = entity.canonical or entity.name
            self.register_entity(entity.name, canonical, entity.aliases)
        self.relations_fwd[fm.id] = list(fm.relations)
        for relation in fm.relations:
            self.relations_rev.setdefault(relation.target, []).append((fm.id, relation))


def neighbor_candidates(index: WikiIndex, fm: WikiFrontmatter, top_k: int = 8) -> list[str]:
    """S5.5 relation curation (extension, additive): candidate doc_ids to
    consider linking `fm` to, ranked by shared entity canonicals (weighted
    higher) and shared tags/keywords. Vector similarity is intentionally not
    used here -- this stays in the cheap, indexing-free metadata index; a
    future version could swap in wiki_summary vector similarity behind the
    same signature.
    """
    fm_canonicals = {e.canonical for e in fm.entities if e.canonical}
    fm_tags = set(fm.tags) | set(fm.keywords)

    scored: list[tuple[int, str]] = []
    for other_id, other_fm in index.docs.items():
        if other_id == fm.id:
            continue
        other_canonicals = {e.canonical for e in other_fm.entities if e.canonical}
        other_tags = set(other_fm.tags) | set(other_fm.keywords)
        overlap = 2 * len(fm_canonicals & other_canonicals) + len(fm_tags & other_tags)
        if overlap > 0:
            scored.append((overlap, other_id))

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [doc_id for _, doc_id in scored[:top_k]]


def load_index(approved_dir: Path) -> WikiIndex:
    index = WikiIndex()
    approved_dir = Path(approved_dir)
    if not approved_dir.exists():
        return index

    loaded: list[WikiFrontmatter] = []
    for path in sorted(approved_dir.glob("*.md")):
        fm, _ = read(path)
        loaded.append(fm)

    # Deterministic processing order so "first document wins" canonical
    # matching (decision D) is reproducible across runs.
    loaded.sort(key=lambda fm: (fm.created_at, fm.id))
    for fm in loaded:
        index.add_document(fm)

    return index
