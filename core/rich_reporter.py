"""Concrete StageReporter (core/progress.py) that logs each S0-S6 stage
transition as a colored, timed line via rich. Split out from core/progress.py
so that module can stay dependency-free; shared by cli.py and api/app.py so
both entry points show the same live progress, not just the CLI.
"""
from __future__ import annotations

import time

from rich.console import Console


class RichReporter:
    """A running log reads better here than a fixed-length progress bar,
    since ingest can run any number of documents through any number of
    verify/regen attempts.
    """

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._started_at: dict[str, float] = {}

    def _label(self, step: str, detail: str) -> str:
        return f"[bold]{step}[/bold] ({detail})" if detail else f"[bold]{step}[/bold]"

    def start(self, step: str, detail: str = "") -> None:
        self._started_at[step] = time.monotonic()
        self.console.print(f"[cyan]▶[/cyan] {self._label(step, detail)}")

    def finish(self, step: str, detail: str = "") -> None:
        elapsed = time.monotonic() - self._started_at.pop(step, time.monotonic())
        self.console.print(f"[green]✓[/green] {self._label(step, detail)} [dim]{elapsed:.1f}s[/dim]")
