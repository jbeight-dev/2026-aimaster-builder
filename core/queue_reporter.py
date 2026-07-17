"""StageReporter (core/progress.py) that pushes each stage event onto a
thread-safe queue instead of printing. api/app.py's /ingest runs the pipeline
on a background thread and drains this queue from a generator to stream
progress back to the calling client over SSE as each stage completes.
"""
from __future__ import annotations

import queue
import time
from typing import Any


class QueueReporter:
    def __init__(self) -> None:
        self.queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._started_at: dict[str, float] = {}

    def start(self, step: str, detail: str = "") -> None:
        self._started_at[step] = time.monotonic()
        self.queue.put({"event": "start", "step": step, "detail": detail})

    def finish(self, step: str, detail: str = "") -> None:
        elapsed = time.monotonic() - self._started_at.pop(step, time.monotonic())
        self.queue.put({"event": "finish", "step": step, "detail": detail, "elapsed": round(elapsed, 1)})

    def close(self) -> None:
        """Sentinel telling the draining generator no more events are coming."""
        self.queue.put(None)
