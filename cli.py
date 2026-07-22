"""wiki CLI - ingest / review list / approve / reindex / index-status.

Thin argparse wiring only: all real logic lives in builder/*.py and is
independently tested. Vendor/config wiring goes through core/factory.py so
nothing here needs to know Azure or Qdrant specifics.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from builder import finalize as finalize_mod
from builder import ops
from builder import pipeline
from builder.review import list_drafts
from core import factory, wiki_io
from core.rich_reporter import RichReporter

_load_context = factory.load_context


def cmd_ingest(args: argparse.Namespace) -> int:
    config, paths = _load_context(args.config)
    llm = factory.build_llm_provider(config)
    entity_types = factory.resolve_entity_types(config)
    relation_types = factory.resolve_relation_types(config)
    max_regen = config.get("verification", {}).get("max_regen", 2)
    translate_enabled = config.get("translation", {}).get("enabled", True)

    doc_ids = pipeline.run_ingest(
        Path(args.path), paths, llm, entity_types, force=args.force,
        relation_types=relation_types, max_regen=max_regen, translate_enabled=translate_enabled,
        reporter=RichReporter(),
    )

    print(f"Ingested '{args.path}' -> {len(doc_ids)} draft document(s):")
    for doc_id in doc_ids:
        print(f"  - {doc_id}")
    print("Run `wiki review list` to inspect drafts, then `wiki approve <doc_id>`.")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    """S0-S6, minus S5.5 (extension, additive): intake through relation
    mapping, skipping S5.5 verification/curation, then writes a S6 draft
    file same as a normal ingest. Intended for callers (e.g. a UI) that want
    a quick preview without paying for the S5.5 verification pass, while
    still entering the normal human-review flow.
    """
    config, paths = _load_context(args.config)
    llm = factory.build_llm_provider(config)
    entity_types = factory.resolve_entity_types(config)
    translate_enabled = config.get("translation", {}).get("enabled", True)

    results = pipeline.run_build(
        Path(args.path), paths, llm, entity_types, translate_enabled=translate_enabled, reporter=RichReporter(),
    )

    print(f"Built '{args.path}' -> {len(results)} document(s) (S0-S6, S5.5 skipped):")
    for r in results:
        flag_note = f" [{len(r.review_flags)} review flag(s)]" if r.review_flags else ""
        print(f"  - {r.doc_id}: {r.frontmatter.title}{flag_note}")
    print("Run `wiki review list` to inspect drafts, then `wiki approve <doc_id>`.")
    print("Staging artifacts (02_structured, 03_enrichment) were written under staging/<source_id>/.")
    return 0


def cmd_review_list(args: argparse.Namespace) -> int:
    _, paths = _load_context(args.config)
    drafts = list_drafts(paths["wiki_draft"])
    if not drafts:
        print("No drafts pending review.")
        return 0
    for d in drafts:
        flag = f" [{d.review_flag_count} REVIEW flag(s)]" if d.review_flag_count else ""
        print(f"{d.doc_id}\tv{d.version}\t{d.title}{flag}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    config, paths = _load_context(args.config)
    embedder = factory.build_embedder(config)
    vector_store = factory.build_vector_store(config)
    namespace = factory.resolve_namespace(config)
    embed_model = config["embedding"]["deployment"]

    fm = finalize_mod.approve_document(
        args.doc_id, paths, embedder, vector_store, namespace, embed_model, config["chunking"],
        reporter=RichReporter(),
    )
    print(f"Approved {fm.id!r} (v{fm.version}) -> wiki/approved/{fm.id}.md, indexed into Qdrant.")
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    config, paths = _load_context(args.config)

    if args.dry_run:
        preview = finalize_mod.preview_reindex(args.doc_id, paths, config["chunking"])
        print(f"[dry-run] {preview}")
        return 0

    embedder = factory.build_embedder(config)
    vector_store = factory.build_vector_store(config)
    namespace = factory.resolve_namespace(config)
    embed_model = config["embedding"]["deployment"]

    fm = finalize_mod.reindex_document(
        args.doc_id, paths, embedder, vector_store, namespace, embed_model, config["chunking"],
        reporter=RichReporter(),
    )
    print(f"Reindexed {fm.id!r} (v{fm.version}).")
    return 0


def cmd_index_status(args: argparse.Namespace) -> int:
    config, paths = _load_context(args.config)
    vector_store = factory.build_vector_store(config)
    counts = vector_store.counts()
    approved_count = len(list((paths["wiki_approved"]).glob("*.md")))
    draft_count = len(list_drafts(paths["wiki_draft"]))

    print(f"approved documents: {approved_count}")
    print(f"pending drafts: {draft_count}")
    for collection, count in counts.items():
        print(f"{collection}: {count} points")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """S5.5 (extension, additive): re-run verification + relation curation
    once against the current draft-or-approved doc, using its existing
    staging artifacts. Useful after a human hand-edits a draft body directly.
    """
    config, paths = _load_context(args.config)
    llm = factory.build_llm_provider(config)
    relation_types = factory.resolve_relation_types(config)
    neighbor_top_k = config.get("verification", {}).get("neighbor_top_k", 8)

    try:
        updated_fm, report = ops.run_verify(args.doc_id, paths, llm, relation_types, neighbor_top_k)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc))
        return 1

    print(report.model_dump_json(indent=2, exclude_none=True))
    print(
        f"Updated {args.doc_id!r}: -> {len(updated_fm.relations)} relations, "
        f"verdict={report.verdict}, value_changes={len(report.value_changes)}"
    )
    return 0


def cmd_relink(args: argparse.Namespace) -> int:
    """`wiki relink` (extension, additive): batch re-curation of relations
    across already-approved documents (e.g. to catch up on links to/from
    documents approved after the fact). Dry-run unless --apply is passed; no
    reindex is needed since relations aren't embedded content.
    """
    config, paths = _load_context(args.config)
    llm = factory.build_llm_provider(config)
    relation_types = factory.resolve_relation_types(config)
    neighbor_top_k = config.get("verification", {}).get("neighbor_top_k", 8)

    if args.all:
        index = wiki_io.load_index(paths["wiki_approved"])
        target_ids = list(index.docs.keys())
    else:
        target_ids = [args.doc_id]

    results = ops.run_relink(target_ids, paths, llm, relation_types, neighbor_top_k, apply=args.apply)
    for r in results:
        print(f"{r.doc_id}: {r.before_count} -> {r.after_count} relations" + (" (changed)" if r.changed else " (no change)"))
        for issue in r.issues:
            print(f"  ! {issue}")
        if r.applied:
            print("  applied")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wiki", description="LLM WIKI Builder CLI")
    parser.add_argument("--config", type=Path, default=None, help="Path to settings.yaml (default: config/settings.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="S0-S6: run a source through the pipeline, producing draft(s)")
    p_ingest.add_argument("path", type=Path)
    p_ingest.add_argument("--force", action="store_true", help="Reprocess even if unchanged since last ingest")
    p_ingest.set_defaults(func=cmd_ingest)

    p_build = sub.add_parser(
        "build",
        help="S0-S6, skipping S5.5 verification/curation; writes a draft same as ingest",
    )
    p_build.add_argument("path", type=Path)
    p_build.set_defaults(func=cmd_build)

    p_review = sub.add_parser("review", help="Review draft documents")
    review_sub = p_review.add_subparsers(dest="review_command", required=True)
    p_review_list = review_sub.add_parser("list", help="List drafts pending review")
    p_review_list.set_defaults(func=cmd_review_list)

    p_approve = sub.add_parser("approve", help="S7-S8: chunk/embed/index a draft and promote it to approved")
    p_approve.add_argument("doc_id")
    p_approve.set_defaults(func=cmd_approve)

    p_reindex = sub.add_parser("reindex", help="Delete and re-upsert an approved document's Qdrant points")
    p_reindex.add_argument("doc_id")
    p_reindex.add_argument("--dry-run", action="store_true", help="Report what would change without touching Qdrant")
    p_reindex.set_defaults(func=cmd_reindex)

    p_status = sub.add_parser("index-status", help="Summarize Qdrant collections and document counts")
    p_status.set_defaults(func=cmd_index_status)

    p_verify = sub.add_parser("verify", help="S5.5: re-run verification + relation curation for one document")
    p_verify.add_argument("doc_id")
    p_verify.set_defaults(func=cmd_verify)

    p_relink = sub.add_parser("relink", help="Batch re-curate relations across approved documents")
    relink_target = p_relink.add_mutually_exclusive_group(required=True)
    relink_target.add_argument("doc_id", nargs="?", default=None)
    relink_target.add_argument("--all", action="store_true", help="Re-curate every approved document")
    p_relink.add_argument("--apply", action="store_true", help="Write changes (default is dry-run)")
    p_relink.set_defaults(func=cmd_relink)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
