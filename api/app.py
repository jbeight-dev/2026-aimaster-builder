"""FastAPI HTTP front end for the same S0-S8 pipeline `cli.py` drives. Thin
routing only, same rule as cli.py: all real logic lives in builder/*.py and
core/*.py, reached here through api/deps.py's Depends() providers so the
exact same functions cli.py calls run underneath.

Run with: uvicorn api.app:app --reload
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, UploadFile
from fastapi import File as FastAPIFile

from api import deps, schemas
from builder import finalize as finalize_mod
from builder import ops, pipeline
from builder.review import list_drafts, read_draft
from core import wiki_io
from core.providers import Embedder, LLMProvider
from core.schemas import WikiFrontmatter

app = FastAPI(title="LLM WIKI Builder API")
router = APIRouter(prefix="/builderapi/v1")


@router.post("/ingest", response_model=schemas.IngestOut)
@router.post("/analyze", response_model=schemas.IngestOut)
def ingest(
    file: UploadFile = FastAPIFile(...),
    force: bool = False,
    config: dict[str, Any] = Depends(deps.get_config),
    paths: dict = Depends(deps.get_paths),
    llm: LLMProvider = Depends(deps.get_llm),
    entity_types: list[str] = Depends(deps.get_entity_types),
    relation_types: list[str] = Depends(deps.get_relation_types),
) -> schemas.IngestOut:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename")

    # Saved at a stable path keyed by filename (not a random temp path):
    # core/ids.py::make_source_id derives source_id from this path string, so
    # re-uploading the same filename must resolve to the same source_id for
    # the CLI's re-ingest idempotency/--force semantics (decision E) to carry
    # over to the API.
    upload_dir = paths["raw"] / "_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = upload_dir / file.filename
    upload_path.write_bytes(file.file.read())

    max_regen = config.get("verification", {}).get("max_regen", 2)
    translate_enabled = config.get("translation", {}).get("enabled", True)
    try:
        doc_ids = pipeline.run_ingest(
            upload_path, paths, llm, entity_types, force=force,
            relation_types=relation_types, max_regen=max_regen, translate_enabled=translate_enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return schemas.IngestOut(doc_ids=doc_ids)


@router.get("/drafts", response_model=list[schemas.DraftOut])
def get_drafts(paths: dict = Depends(deps.get_paths)) -> list[schemas.DraftOut]:
    drafts = list_drafts(paths["wiki_draft"])
    return [
        schemas.DraftOut(
            doc_id=d.doc_id, title=d.title, version=d.version, review_flag_count=d.review_flag_count
        )
        for d in drafts
    ]


@router.get("/documents/{doc_id}", response_model=schemas.DocumentOut)
def get_document(
    doc_id: str,
    paths: dict = Depends(deps.get_paths),
) -> schemas.DocumentOut:
    try:
        fm, body = read_draft(paths["wiki_draft"], doc_id)
    except FileNotFoundError:
        try:
            fm, body = wiki_io.read(paths["wiki_approved"] / f"{doc_id}.md")
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"No document found for doc_id={doc_id!r}") from exc
    return schemas.DocumentOut(document=fm, body=body)


@router.post("/documents/{doc_id}/approve", response_model=WikiFrontmatter)
def approve(
    doc_id: str,
    config: dict[str, Any] = Depends(deps.get_config),
    paths: dict = Depends(deps.get_paths),
    embedder: Embedder = Depends(deps.get_embedder),
    vector_store=Depends(deps.get_vector_store),
    namespace=Depends(deps.get_namespace),
) -> WikiFrontmatter:
    embed_model = config["embedding"]["deployment"]
    try:
        return finalize_mod.approve_document(
            doc_id, paths, embedder, vector_store, namespace, embed_model, config["chunking"]
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/documents/{doc_id}/reindex")
def reindex(
    doc_id: str,
    dry_run: bool = False,
    config: dict[str, Any] = Depends(deps.get_config),
    paths: dict = Depends(deps.get_paths),
    embedder: Embedder = Depends(deps.get_embedder),
    vector_store=Depends(deps.get_vector_store),
    namespace=Depends(deps.get_namespace),
) -> schemas.ReindexPreviewOut | WikiFrontmatter:
    try:
        if dry_run:
            preview = finalize_mod.preview_reindex(doc_id, paths, config["chunking"])
            return schemas.ReindexPreviewOut(**preview)
        embed_model = config["embedding"]["deployment"]
        return finalize_mod.reindex_document(
            doc_id, paths, embedder, vector_store, namespace, embed_model, config["chunking"]
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/index-status", response_model=schemas.IndexStatusOut)
def index_status(
    paths: dict = Depends(deps.get_paths),
    vector_store=Depends(deps.get_vector_store),
) -> schemas.IndexStatusOut:
    counts = vector_store.counts()
    approved_count = len(list(paths["wiki_approved"].glob("*.md")))
    draft_count = len(list_drafts(paths["wiki_draft"]))
    return schemas.IndexStatusOut(
        approved_documents=approved_count, pending_drafts=draft_count, collections=counts
    )


@router.post("/documents/{doc_id}/verify", response_model=schemas.VerifyOut)
def verify(
    doc_id: str,
    config: dict[str, Any] = Depends(deps.get_config),
    paths: dict = Depends(deps.get_paths),
    llm: LLMProvider = Depends(deps.get_llm),
    relation_types: list[str] = Depends(deps.get_relation_types),
) -> schemas.VerifyOut:
    neighbor_top_k = config.get("verification", {}).get("neighbor_top_k", 8)
    try:
        updated_fm, report = ops.run_verify(doc_id, paths, llm, relation_types, neighbor_top_k)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return schemas.VerifyOut(document=updated_fm, report=report)


@router.post("/relink", response_model=list[schemas.RelinkResultOut])
def relink(
    body: schemas.RelinkRequest,
    config: dict[str, Any] = Depends(deps.get_config),
    paths: dict = Depends(deps.get_paths),
    llm: LLMProvider = Depends(deps.get_llm),
    relation_types: list[str] = Depends(deps.get_relation_types),
) -> list[schemas.RelinkResultOut]:
    if not body.all and not body.doc_id:
        raise HTTPException(status_code=400, detail="Provide doc_id or set all=true")

    neighbor_top_k = config.get("verification", {}).get("neighbor_top_k", 8)
    if body.all:
        index = wiki_io.load_index(paths["wiki_approved"])
        target_ids = list(index.docs.keys())
    else:
        target_ids = [body.doc_id]

    results = ops.run_relink(target_ids, paths, llm, relation_types, neighbor_top_k, apply=body.apply)
    return [schemas.RelinkResultOut(**vars(r)) for r in results]


app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()
    uvicorn.run(
        "api.app:app",

        reload=True,
    )
