"""Review Agent (external-facing, read-only). Compares a Wiki Draft against
its original source document for Faithfulness/Completeness/Value-Changes and
composes a narrative review comment to support a human's approve/revise
decision. Orchestration (locating the doc, calling the LLM, persisting the
report) lives in builder/ops.py::run_review_agent; this module only holds the
comment-composition logic specific to this agent's output.
"""
from __future__ import annotations

from core.schemas import Finding, ValueChange


def compose_review_comment(
    faithfulness: list[Finding],
    completeness: list[str],
    value_changes: list[ValueChange],
) -> str:
    ungrounded = [f for f in faithfulness if not f.grounded]
    if not ungrounded and not completeness and not value_changes:
        return "AI 검토 결과, 원본 문서와 비교하여 사실 불일치 또는 주요 내용의 누락이 발견되지 않았습니다. 승인해도 무방합니다."

    parts: list[str] = []
    if ungrounded:
        detail = "; ".join(f"[{f.severity}] {f.claim}" for f in ungrounded)
        parts.append(f"사실 불일치(Faithfulness) {len(ungrounded)}건: {detail}")
    if completeness:
        detail = "; ".join(completeness)
        parts.append(f"누락된 내용(Completeness) {len(completeness)}건: {detail}")
    if value_changes:
        detail = "; ".join(
            f"{vc.kind} {vc.original_value!r} -> {vc.changed_value!r}" for vc in value_changes
        )
        parts.append(f"왜곡된 값(Value Changes) {len(value_changes)}건: {detail}")
    parts.append("위 항목을 확인 후 필요한 부분을 수정하여 재검토하시기 바랍니다.")
    return " ".join(parts)
