"""Rich live dashboard for monitoring jobs (preserved from JioFarm's monitoring)."""

from __future__ import annotations

import time
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .models import Job, JobState
from .storage.store import Store

_STATE_STYLE = {
    JobState.DRAFT: "dim",
    JobState.CONFIRMED: "cyan",
    JobState.AWAITING_HUMAN_ACTION: "yellow",
    JobState.IN_REVIEW: "magenta",
    JobState.COMPLETED: "bold green",
    JobState.FAILED: "bold red",
    JobState.CANCELLED: "red",
}


def _short(dt: Optional[str]) -> str:
    if not dt:
        return "-"
    return dt.replace("T", " ")[:19]


def render_table(jobs: list[Job]) -> Table:
    table = Table(title="Family Link Worker — Jobs", expand=True, header_style="bold")
    table.add_column("Job ID", no_wrap=True)
    table.add_column("Op")
    table.add_column("Child")
    table.add_column("State")
    table.add_column("Result")
    table.add_column("Attempts", justify="right")
    table.add_column("Updated")
    for job in jobs:
        style = _STATE_STYLE.get(job.state, "white")
        table.add_row(
            job.id,
            job.operation.value,
            job.child_display_name,
            Text(job.state.value, style=style),
            job.result.value if job.result else "-",
            f"{job.attempts}/{job.max_attempts}",
            _short(job.updated_at),
        )
    if not jobs:
        table.add_row("—", "—", "No jobs yet", "—", "—", "—", "—")
    return table


def summary_panel(jobs: list[Job]) -> Panel:
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.state.value] = counts.get(job.state.value, 0) + 1
    parts = [f"{k}={v}" for k, v in sorted(counts.items())]
    body = "  ".join(parts) if parts else "no jobs"
    return Panel(body, title=f"Total: {len(jobs)}", border_style="blue")


def live_monitor(store: Store, console: Console, refresh_seconds: float = 2.0) -> None:
    """Blocking live view. Ctrl-C to exit."""
    with Live(console=console, refresh_per_second=4, screen=False) as live:
        try:
            while True:
                jobs = store.list_jobs()
                grid = Table.grid(expand=True)
                grid.add_row(summary_panel(jobs))
                grid.add_row(render_table(jobs))
                live.update(grid)
                time.sleep(refresh_seconds)
        except KeyboardInterrupt:
            console.print("\n[dim]Monitor stopped.[/dim]")
