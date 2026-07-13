#!/usr/bin/env bash
# End-to-end demo: ingest all 5 sample formats -> review -> approve -> index,
# then demonstrate idempotent re-ingest and a reindex overwrite.
#
# Uses config/settings.fake.yaml (deterministic Fake LLM/Embedder) so this
# runs with no network access and no AOAI_API_KEY. Pass --config
# config/settings.yaml to run the same flow against real Azure OpenAI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python}"
CONFIG="${1:-config/settings.fake.yaml}"
WIKI() { "$PYTHON" cli.py --config "$CONFIG" "$@"; }

echo "== Regenerating binary sample fixtures (pdf, sqlite) =="
"$PYTHON" scripts/generate_samples.py

echo
echo "== 1. Ingest all 5 sample formats (S0-S6) =="
declare -a DOC_IDS=()
for sample in samples/pdf/manual.pdf samples/csv/dataset.csv samples/txt/note.txt samples/md/doc.md samples/sqlite/sample.db; do
  echo "--- ingest $sample ---"
  out="$(WIKI ingest "$sample" | tr -d '\r')"
  echo "$out"
  while IFS= read -r line; do
    if [[ "$line" =~ ^\ \ -\ (.+)$ ]]; then
      DOC_IDS+=("${BASH_REMATCH[1]}")
    fi
  done <<< "$out"
done

echo
echo "== 2. Review queue (S6) =="
WIKI review list

echo
echo "== 3. Approve every draft (S7-S8) =="
for doc_id in "${DOC_IDS[@]}"; do
  WIKI approve "$doc_id"
done

echo
echo "== 4. Index status =="
WIKI index-status

echo
echo "== 5. Idempotency check: re-ingest one unchanged source =="
WIKI ingest samples/txt/note.txt

echo
echo "== 6. Reindex overwrite check (dry-run then real) =="
first_doc="${DOC_IDS[0]}"
WIKI reindex "$first_doc" --dry-run
WIKI reindex "$first_doc"
WIKI index-status

echo
echo "Demo complete."
