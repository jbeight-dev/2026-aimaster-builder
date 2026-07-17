"""Stage-progress reporting hook for long-running pipeline runs (S0-S6).

Kept dependency-free so builder/*.py and graphs/*.py never need to import a
rendering library -- only cli.py's RichReporter does that. Callers that don't
care about progress (tests, the LangGraph wrapper) simply omit the reporter
and get NULL_REPORTER's no-ops.
"""
from __future__ import annotations

from typing import Protocol


class StageReporter(Protocol):
    def start(self, step: str, detail: str = "") -> None: ...
    def finish(self, step: str, detail: str = "") -> None: ...


class NullReporter:
    def start(self, step: str, detail: str = "") -> None:
        pass

    def finish(self, step: str, detail: str = "") -> None:
        pass


NULL_REPORTER = NullReporter()


class CompositeReporter:
    """Fans out each event to multiple reporters -- e.g. api/app.py's /ingest
    keeps logging to the server console (RichReporter) while also queuing
    events for the calling client (QueueReporter's SSE stream).
    """

    def __init__(self, reporters: list[StageReporter]) -> None:
        self._reporters = reporters

    def start(self, step: str, detail: str = "") -> None:
        for r in self._reporters:
            r.start(step, detail)

    def finish(self, step: str, detail: str = "") -> None:
        for r in self._reporters:
            r.finish(step, detail)
