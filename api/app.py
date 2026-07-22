"""FastAPI HTTP front end for the same S0-S8 pipeline `cli.py` drives. Thin
routing only, same rule as cli.py: all real logic lives in builder/*.py and
core/*.py, reached here through api/deps.py's Depends() providers so the
exact same functions cli.py calls run underneath.

Run with: uvicorn api.app:app --reload
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Iterator

from fastapi import APIRouter, Depends, FastAPI, HTTPException, UploadFile
from fastapi import File as FastAPIFile
from fastapi.responses import StreamingResponse

from api import deps, schemas
from builder import finalize as finalize_mod
from builder import ops, pipeline
from builder.review import list_drafts, read_draft
from core import wiki_io
from core.progress import CompositeReporter
from core.providers import Embedder, LLMProvider
from core.queue_reporter import QueueReporter
from core.rich_reporter import RichReporter
from core.schemas import WikiFrontmatter

app = FastAPI(title="LLM WIKI Builder API")
router = APIRouter(prefix="/builderapi/v1")


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/ingest")
@router.post("/analyze")
def ingest(
    file: UploadFile = FastAPIFile(...),
    force: bool = False,
    config: dict[str, Any] = Depends(deps.get_config),
    paths: dict = Depends(deps.get_paths),
    llm: LLMProvider = Depends(deps.get_llm),
    entity_types: list[str] = Depends(deps.get_entity_types),
    relation_types: list[str] = Depends(deps.get_relation_types),
) -> StreamingResponse:
    """Streams S0-S6 stage progress back to the caller over SSE as each stage
    completes, instead of blocking silently until the whole (potentially
    multi-minute, real-LLM) run finishes. The HTTP status is always 200 once
    the stream starts (SSE has no way to change it mid-stream); a failed run
    is reported in-band as a final `{"event": "error", ...}` message instead
    of an HTTP error status, so callers must check the event type of the last
    message rather than the status code.
    """
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

    queue_reporter = QueueReporter()
    reporter = CompositeReporter([RichReporter(), queue_reporter])

    def run() -> None:
        try:
            doc_ids = pipeline.run_ingest(
                upload_path, paths, llm, entity_types, force=force,
                relation_types=relation_types, max_regen=max_regen, translate_enabled=translate_enabled,
                reporter=reporter,
            )
            queue_reporter.queue.put({"event": "result", **schemas.IngestOut(doc_ids=doc_ids).model_dump()})
        except ValueError as exc:
            queue_reporter.queue.put({"event": "error", "detail": str(exc)})
        finally:
            queue_reporter.close()

    threading.Thread(target=run, daemon=True).start()

    def event_stream() -> Iterator[str]:
        while (item := queue_reporter.queue.get()) is not None:
            yield _sse(item)

    return StreamingResponse(event_stream(), media_type="text/event-stream")

# Step2. Build endpoint
@router.post("/build")
def build(
    file: UploadFile = FastAPIFile(...),
    config: dict[str, Any] = Depends(deps.get_config),
    paths: dict = Depends(deps.get_paths),
    llm: LLMProvider = Depends(deps.get_llm),
    entity_types: list[str] = Depends(deps.get_entity_types),
) -> StreamingResponse:
    """Streams S0-S6 stage progress over SSE (same transport as /ingest), but
    skips S5.5 verification/curation: relations stay raw/uncurated and no
    regen loop runs. A S6 draft file is still written for each document, same
    as /ingest, so downstream (approve/verify/reindex) can resume from it via
    doc_id. The final `result` event carries each document's frontmatter/body/
    review_flags directly (in addition to the draft already on disk).
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename")

    upload_dir = paths["raw"] / "_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = upload_dir / file.filename
    upload_path.write_bytes(file.file.read())

    translate_enabled = config.get("translation", {}).get("enabled", True)

    queue_reporter = QueueReporter()
    reporter = CompositeReporter([RichReporter(), queue_reporter])

    def run() -> None:
        try:
            results = pipeline.run_build(
                upload_path, paths, llm, entity_types, translate_enabled=translate_enabled, reporter=reporter,
            )
            out = schemas.BuildOut(
                documents=[
                    schemas.BuildDocumentOut(
                        doc_id=r.doc_id, document=r.frontmatter, body=r.structured_md, review_flags=r.review_flags,
                    )
                    for r in results
                ]
            )
            queue_reporter.queue.put({"event": "result", **out.model_dump(mode="json")})
        except ValueError as exc:
            queue_reporter.queue.put({"event": "error", "detail": str(exc)})
        finally:
            queue_reporter.close()

    threading.Thread(target=run, daemon=True).start()

    def event_stream() -> Iterator[str]:
        while (item := queue_reporter.queue.get()) is not None:
            yield _sse(item)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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

# Step3. Approve
@router.post("/documents/{doc_id}/approve")
def approve(
    doc_id: str,
    config: dict[str, Any] = Depends(deps.get_config),
    paths: dict = Depends(deps.get_paths),
    embedder: Embedder = Depends(deps.get_embedder),
    vector_store=Depends(deps.get_vector_store),
    namespace=Depends(deps.get_namespace),
) -> StreamingResponse:
    """Chunks, embeds, and indexes the draft document, then promotes it to
    approved. Streams S7-S8 progress over SSE."""
    embed_model = config["embedding"]["deployment"]
    queue_reporter = QueueReporter()
    reporter = CompositeReporter([RichReporter(), queue_reporter])

    def run() -> None:
        try:
            fm = finalize_mod.approve_document(
                doc_id, paths, embedder, vector_store, namespace, embed_model, config["chunking"],
                reporter=reporter,
            )
            queue_reporter.queue.put({"event": "result", "document": fm.model_dump()})
        except FileNotFoundError as exc:
            queue_reporter.queue.put({"event": "error", "detail": str(exc)})
        except ValueError as exc:
            queue_reporter.queue.put({"event": "error", "detail": str(exc)})
        finally:
            queue_reporter.close()

    threading.Thread(target=run, daemon=True).start()

    def event_stream() -> Iterator[str]:
        while (item := queue_reporter.queue.get()) is not None:
            yield _sse(item)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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

# AI 검토 의견
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
        updated_fm, report = ops.run_verify(
            doc_id, paths, llm, relation_types, neighbor_top_k,
            reporter=RichReporter(),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return schemas.VerifyOut(document=updated_fm, report=report)


# Review Agent: read-only Faithfulness/Completeness check to support approval
@router.post("/documents/{doc_id}/verify_agent", response_model=schemas.ReviewAgentOut)
def verify_agent(
    doc_id: str,
    paths: dict = Depends(deps.get_paths),
    llm: LLMProvider = Depends(deps.get_llm),
) -> schemas.ReviewAgentOut:
    try:
        report = ops.run_review_agent(doc_id, paths, llm, reporter=RichReporter())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return schemas.ReviewAgentOut(report=report)


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
