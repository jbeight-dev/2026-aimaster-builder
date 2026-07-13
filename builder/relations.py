"""S5 - Relation Mapping. Deterministic only for this PoC (decision J /
plan scope note): sqlite foreign keys become `foreign_key` relations, and
documents sharing a resolved entity canonical get a `see_also` relation.
LLM-assisted relation judgment (config: relations.use_llm) is left as a future
extension point and is not implemented.
"""
from __future__ import annotations

from core.schemas import ExtractedDoc, Relation, WikiFrontmatter
from core.wiki_io import WikiIndex


def map_relations(doc: ExtractedDoc, fm: WikiFrontmatter, index: WikiIndex) -> list[Relation]:
    relations: list[Relation] = []

    for block in doc.blocks:
        for fk in block.meta.get("foreign_keys", []):
            target_source_id = f"{doc.source_id}__{fk['table']}"
            target_doc_id = index.by_source_id.get(target_source_id)
            if target_doc_id and target_doc_id != fm.id:
                rel = Relation(type="foreign_key", target=target_doc_id)
                if rel not in relations:
                    relations.append(rel)

    shared_canonicals = {e.canonical for e in fm.entities if e.canonical}
    if shared_canonicals:
        for other_id, other_fm in index.docs.items():
            if other_id == fm.id:
                continue
            other_canonicals = {e.canonical for e in other_fm.entities if e.canonical}
            if shared_canonicals & other_canonicals:
                rel = Relation(type="see_also", target=other_id)
                if rel not in relations:
                    relations.append(rel)

    return relations
