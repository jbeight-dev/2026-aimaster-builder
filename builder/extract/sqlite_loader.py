"""sqlite -> one ExtractedDoc per table. Schema/FK info goes into a caption
paragraph and Block.meta["foreign_keys"] (consumed later by S5 relations.py to
mint deterministic foreign_key relations); row sample becomes a table block.
Every table converges on the same Block schema as every other format -- no
tabular-specific document type.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core.schemas import Block, ExtractedDoc

MAX_ROWS = 200


class SqliteLoader:
    def load(self, path: Path, source_id: str, title_hint: str) -> list[ExtractedDoc]:
        # title_hint (the .db filename) isn't used -- each table's own name is
        # already a more meaningful per-document title than the database file.
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
            docs: list[ExtractedDoc] = []
            for table in tables:
                docs.append(self._load_table(conn, table, source_id))
            return docs
        finally:
            conn.close()

    def _load_table(self, conn: sqlite3.Connection, table: str, source_id: str) -> ExtractedDoc:
        columns_info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        fk_info = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()

        col_names = [c["name"] for c in columns_info]
        col_desc = ", ".join(f"{c['name']} ({c['type'] or 'ANY'})" for c in columns_info)
        pk_cols = [c["name"] for c in columns_info if c["pk"]]

        foreign_keys = [
            {"from": fk["from"], "table": fk["table"], "to": fk["to"]} for fk in fk_info
        ]

        row_count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        rows = conn.execute(f'SELECT * FROM "{table}" LIMIT {MAX_ROWS}').fetchall()

        caption_parts = [f"Table '{table}' with {row_count} rows. Columns: {col_desc}."]
        if pk_cols:
            caption_parts.append(f"Primary key: {', '.join(pk_cols)}.")
        if foreign_keys:
            fk_desc = "; ".join(f"{fk['from']} -> {fk['table']}.{fk['to']}" for fk in foreign_keys)
            caption_parts.append(f"Foreign keys: {fk_desc}.")

        blocks = [
            Block(type="heading", level=1, text=table),
            Block(
                type="paragraph",
                text=" ".join(caption_parts),
                meta={"foreign_keys": foreign_keys, "table": table},
            ),
            Block(
                type="table",
                header=col_names,
                rows=[[str(row[c]) if row[c] is not None else "" for c in col_names] for row in rows],
                caption=table,
                meta={"foreign_keys": foreign_keys, "table": table},
            ),
        ]

        doc_id = f"{source_id}__{table}"
        return ExtractedDoc(
            doc_id=doc_id,
            source_id=source_id,
            source_type="sqlite",
            title=table,
            blocks=blocks,
        )
