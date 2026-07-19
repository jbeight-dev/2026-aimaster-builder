"""S5 - Space-scoped Relation Mapping (mockup / PoC).

There is no `space_id` field on the schema yet (WikiFrontmatter, SourceRef,
ExtractedDoc). This module is a standalone mockup of what space-scoped
relation mapping would look like: it reads `space_id` out of
`SourceRef.locator` (a free-form dict already used for source-specific
metadata), so no schema change is needed to try this out. Documents without a
`space_id` in their locator are skipped entirely -- they neither get nor
receive space relations.

Not wired into builder/pipeline.py. Once a real `space_id` field lands on the
schema, `get_space_id` is the only place that needs to change.
"""
from __future__ import annotations

from core.schemas import Relation, WikiFrontmatter
from core.wiki_io import WikiIndex


def get_space_id(fm: WikiFrontmatter) -> str | None:
    """Mock accessor: `space_id` lives in `source.locator` until a real field
    is added to WikiFrontmatter/SourceRef.
    """
    space_id = fm.source.locator.get("space_id")
    return str(space_id) if space_id else None


def map_relations_by_space(fm: WikiFrontmatter, index: WikiIndex) -> list[Relation]:
    """Links `fm` to every other indexed document sharing the same
    `space_id`, as `linked_document` relations (chosen because it's the one
    RelationType left unused by builder/relations.py's foreign_key/see_also
    mapping).
    """
    relations: list[Relation] = []

    space_id = get_space_id(fm)
    if not space_id:
        return relations

    for other_id, other_fm in index.docs.items():
        if other_id == fm.id:
            continue
        if get_space_id(other_fm) == space_id:
            rel = Relation(type="linked_document", target=other_id)
            if rel not in relations:
                relations.append(rel)

    return relations
