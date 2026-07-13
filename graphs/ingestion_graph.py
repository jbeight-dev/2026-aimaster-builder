"""Thin LangGraph wrapper over builder/pipeline.py's plain functions. All
business logic stays in builder/*.py (independently unit-tested without this
graph); this module only wires those same functions as nodes so the project
satisfies the stated LangGraph tech-stack choice for S0-S6 orchestration.

S6 has no live "reject" loop-back edge here: decision B moves HITL rejection
out-of-band (a human edits wiki/draft/{doc_id}.md directly, or re-runs
`wiki ingest --force` after fixing the source) rather than suspending an
in-process graph run waiting on a signal. A checkpointer is accepted for
intra-run crash recovery but is optional and off by default -- cross-run
resumability's source of truth is manifest.json (decision E), not this graph.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from builder import pipeline
from builder.extract.base import run_extraction
from builder.intake import run_intake
from core import manifest as manifest_io
from core.providers import LLMProvider
from core.wiki_io import load_index


class IngestState(TypedDict, total=False):
    path: str
    paths: dict[str, Any]
    force: bool
    intake_result: Any
    manifest: Any
    extraction: Any
    doc_ids: list[str]
    skip: bool


def build_ingestion_graph(
    llm: LLMProvider,
    entity_types: list[str],
    checkpointer: Any = None,
    relation_types: list[str] | None = None,
    max_regen: int = 2,
    translate_enabled: bool = True,
):
    def intake_node(state: IngestState) -> dict[str, Any]:
        path = Path(state["path"])
        paths = state["paths"]
        intake_result = run_intake(path, paths["raw"], paths["staging"])
        manifest = manifest_io.load_or_init(paths["staging"], intake_result.source_id, str(path))
        skip = (
            not state.get("force", False)
            and not intake_result.changed
            and manifest_io.resume_step(manifest) is None
        )
        return {"intake_result": intake_result, "manifest": manifest, "skip": skip}

    def extract_node(state: IngestState) -> dict[str, Any]:
        intake_result = state["intake_result"]
        paths = state["paths"]
        extraction = run_extraction(
            intake_result.raw_path,
            intake_result.source_id,
            intake_result.source_type,
            paths["staging"],
            title_hint=Path(intake_result.original_path).stem,
        )
        manifest_io.mark(state["manifest"], "extract", "done")
        return {"extraction": extraction}

    def process_documents_node(state: IngestState) -> dict[str, Any]:
        paths = state["paths"]
        manifest = state["manifest"]
        index = load_index(paths["wiki_approved"])
        doc_ids: list[str] = []
        try:
            for doc in state["extraction"].documents:
                doc_ids.append(
                    pipeline.process_document(
                        doc, state["intake_result"], paths, llm, entity_types, index, relation_types, max_regen,
                        translate_enabled,
                    )
                )
            for step in ("structure", "translate", "enrich", "metadata", "relations", "draft"):
                manifest_io.mark(manifest, step, "done")
            manifest.doc_ids = doc_ids
        except Exception as exc:
            manifest_io.mark(manifest, manifest_io.resume_step(manifest) or "draft", "failed", detail=str(exc))
            manifest_io.save(paths["staging"], manifest)
            raise
        manifest_io.save(paths["staging"], manifest)
        return {"doc_ids": doc_ids}

    def skip_node(state: IngestState) -> dict[str, Any]:
        return {"doc_ids": state["manifest"].doc_ids}

    graph = StateGraph(IngestState)
    graph.add_node("intake", intake_node)
    graph.add_node("extract", extract_node)
    graph.add_node("process_documents", process_documents_node)
    graph.add_node("skip", skip_node)

    graph.set_entry_point("intake")
    graph.add_conditional_edges(
        "intake", lambda s: "skip" if s.get("skip") else "extract", {"skip": "skip", "extract": "extract"}
    )
    graph.add_edge("extract", "process_documents")
    graph.add_edge("process_documents", END)
    graph.add_edge("skip", END)

    return graph.compile(checkpointer=checkpointer)


def run_ingest_via_graph(
    path: Path,
    paths: dict[str, Any],
    llm: LLMProvider,
    entity_types: list[str],
    force: bool = False,
    checkpointer: Any = None,
    relation_types: list[str] | None = None,
    max_regen: int = 2,
    translate_enabled: bool = True,
) -> list[str]:
    compiled = build_ingestion_graph(
        llm, entity_types, checkpointer=checkpointer, relation_types=relation_types, max_regen=max_regen,
        translate_enabled=translate_enabled,
    )
    config = {"configurable": {"thread_id": str(path)}} if checkpointer else None
    result = compiled.invoke({"path": str(path), "paths": paths, "force": force}, config=config)
    return result["doc_ids"]
