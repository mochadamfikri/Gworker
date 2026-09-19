"""Typer/Rich CLI for the Google Family Link Assistant Worker.

Commands:
    familylink init
    familylink family-head login
    familylink job create
    familylink jobs
    familylink confirm JOB_ID --result ...
    familylink resume JOB_ID
    familylink cancel JOB_ID
    familylink audit JOB_ID
    familylink monitor
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .config import Config
from .crypto import SecretBox
from .models import (
    FailureType,
    JobResult,
    Operation,
    WorkerError,
    fingerprint,
)
from .monitor import live_monitor, render_table
from .redact import mask_email
from .service import FamilyLinkService
from .storage.store import Store
from .workflow import build_handoff

app = typer.Typer(
    add_completion=False,
    help="Hardened, human-in-the-loop assistant for authorized Google Family Link actions.",
    no_args_is_help=True,
)
family_head_app = typer.Typer(help="Manage the family-head (guardian) identifier.", no_args_is_help=True)
job_app = typer.Typer(help="Create Family Link jobs.", no_args_is_help=True)
app.add_typer(family_head_app, name="family-head")
app.add_typer(job_app, name="job")

console = Console()


def _ctx() -> tuple[Config, Store, FamilyLinkService, SecretBox]:
    config = Config.load()
    config.ensure_home()
    store = Store(config.db_path)
    box = SecretBox(config.key_path)
    return config, store, FamilyLinkService(store, config), box


def _fail(message: str) -> None:
    console.print(Panel(message, title="Cannot continue", border_style="red"))
    raise typer.Exit(code=1)


def _resolve_family_head(store: Store, family_head: Optional[str]) -> str:
    heads = store.list_family_heads()
    if not heads:
        _fail("No family head registered. Run `familylink family-head login` first.")
    if family_head:
        head = store.get_family_head(family_head) or store.get_family_head_by_fingerprint(fingerprint(family_head))
        if not head:
            _fail(f"Family head '{family_head}' not found.")
        return head.id
    if len(heads) == 1:
        return heads[0].id
    console.print("[yellow]Multiple family heads found. Pass --family-head <id>:[/yellow]")
    for h in heads:
        console.print(f"  • {h.id}  (fp={h.identifier_fingerprint[:8]}...)")
    raise typer.Exit(code=1)


# --------------------------------------------------------------------- init
@app.command()
def init() -> None:
    """Initialize the local database and private key store (perms 0600)."""
    config, store, _, box = _ctx()
    store.add_audit("worker.init", "Worker initialized.")
    console.print(
        Panel(
            f"Home: [bold]{config.home}[/bold]\n"
            f"Database: {config.db_path}\n"
            f"Key file: {config.key_path}\n\n"
            "[dim]No passwords, cookies, or tokens are ever stored. "
            "Login, consent, OTP and CAPTCHA always happen in Google's official UI.[/dim]",
            title="Family Link Worker initialized",
            border_style="green",
        )
    )
    store.close()


# ---------------------------------------------------------- family-head login
@family_head_app.command("login")
def family_head_login(
    identifier: Optional[str] = typer.Option(None, "--identifier", "-i", help="Guardian account identifier (e.g. email)."),
    label: Optional[str] = typer.Option(None, "--label", "-l", help="Friendly label for this household."),
) -> None:
    """Register the family-head identifier locally (encrypted). No password is asked."""
    _, store, service, box = _ctx()
    if not identifier:
        identifier = Prompt.ask("Family Head identifier (email)")
    identifier = identifier.strip()
    if not identifier:
        _fail("An identifier is required.")
    console.print(
        "[dim]This only records who the guardian is. You will sign in to Google yourself, "
        "in Google's official UI. This tool never asks for your password.[/dim]"
    )
    enc = box.encrypt(identifier)
    head = service.register_family_head(enc, fingerprint(identifier), label)
    console.print(
        Panel(
            f"Family head registered.\nID: [bold]{head.id}[/bold]\nIdentifier: {mask_email(identifier)}",
            title="family-head login",
            border_style="green",
        )
    )
    store.close()


# --------------------------------------------------------------- job create
@job_app.command("create")
def job_create(
    child: Optional[str] = typer.Option(None, "--child", "-c", help="Child display name."),
    birth_date: Optional[str] = typer.Option(None, "--birth-date", "-b", help="Child birth date YYYY-MM-DD."),
    operation: Operation = typer.Option(Operation.CREATE, "--operation", "-o", help="create|link|member-status|cancel"),
    guardian: Optional[str] = typer.Option(None, "--guardian", "-g", help="Legal guardian's name."),
    relationship: Optional[str] = typer.Option(None, "--relationship", "-r", help="Guardian relationship to child."),
    family_head: Optional[str] = typer.Option(None, "--family-head", "-f", help="Family head id or identifier."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the interactive confirmation screen."),
) -> None:
    """Create a job, validate locally, show a confirmation screen, then hand off to Google."""
    config, store, service, box = _ctx()
    fh_id = _resolve_family_head(store, family_head)

    if not child:
        child = Prompt.ask("Child display name")
    if not birth_date:
        birth_date = Prompt.ask("Child birth date (YYYY-MM-DD)")
    if not guardian:
        guardian = Prompt.ask("Legal guardian's name")

    # Local validation preview (before we ask for consent/confirmation).
    try:
        from .validation import validate_birth_date

        normalized_bd = validate_birth_date(birth_date)
    except WorkerError as exc:
        store.close()
        _fail(str(exc))

    # Confirmation screen summarizing the child data.
    table = Table(title="Confirm child data", show_header=False, border_style="cyan")
    table.add_row("Operation", operation.value)
    table.add_row("Child display name", child)
    table.add_row("Birth date (as supplied)", normalized_bd)
    table.add_row("Legal guardian", guardian)
    table.add_row("Relationship", relationship or "-")
    console.print(table)
    console.print(
        "[yellow]Guardian authorization is required. The legal guardian must complete Google's "
        "consent and verification steps manually.[/yellow]"
    )

    if not yes:
        consent = Confirm.ask("Does the legal guardian authorize this action and confirm the data above?")
        if not consent:
            store.close()
            _fail("Guardian consent not given. No job created.")

    try:
        job = service.create_job(
            family_head_id=fh_id,
            child_display_name=child,
            birth_date_raw=birth_date,
            operation=operation,
            guardian_name=guardian,
            consent_given=True,
            relationship=relationship,
        )
    except WorkerError as exc:
        store.close()
        _fail(str(exc))

    # Move to awaiting-human and print the official handoff.
    job = service.confirm_job(job.id)
    handoff = build_handoff(config, operation)
    steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(handoff.steps))
    console.print(
        Panel(
            f"[bold]{handoff.title}[/bold]\n\n"
            f"Official URL: [link]{handoff.url}[/link]\n\n"
            f"{steps}\n\n[dim]{handoff.reminder}[/dim]\n\n"
            f"When done, record the outcome:\n"
            f"  [green]familylink confirm {job.id} --result created|linked|rejected|pending|unknown[/green]",
            title=f"Job {job.id} — official Google handoff",
            border_style="green",
        )
    )
    store.close()


# --------------------------------------------------------------------- jobs
@app.command()
def jobs(family_head: Optional[str] = typer.Option(None, "--family-head", "-f")) -> None:
    """List all jobs (optionally for one family head)."""
    _, store, _, _ = _ctx()
    fh_id = None
    if family_head:
        head = store.get_family_head(family_head) or store.get_family_head_by_fingerprint(fingerprint(family_head))
        fh_id = head.id if head else family_head
    console.print(render_table(store.list_jobs(fh_id)))
    store.close()


# ------------------------------------------------------------------ confirm
_RESULT_ALIASES = {
    "created": JobResult.CREATED,
    "linked": JobResult.LINKED,
    "pending": JobResult.PENDING_HUMAN_ACTION,
    "pending_human_action": JobResult.PENDING_HUMAN_ACTION,
    "rejected": JobResult.REJECTED,
    "cancelled": JobResult.CANCELLED,
    "unknown": JobResult.UNKNOWN_REQUIRES_REVIEW,
    "unknown_requires_review": JobResult.UNKNOWN_REQUIRES_REVIEW,
}


@app.command()
def confirm(
    job_id: str = typer.Argument(..., help="Job ID."),
    result: str = typer.Option(..., "--result", "-r", help="created|linked|pending|rejected|cancelled|unknown"),
) -> None:
    """Record the actual outcome the guardian obtained from Google's official UI."""
    _, store, service, _ = _ctx()
    key = result.strip().lower()
    if key not in _RESULT_ALIASES:
        store.close()
        _fail(f"Unknown result '{result}'. Use one of: {', '.join(sorted(_RESULT_ALIASES))}.")
    try:
        job = service.record_result(job_id, _RESULT_ALIASES[key])
    except WorkerError as exc:
        store.close()
        _fail(str(exc))
    console.print(
        Panel(
            f"Job [bold]{job.id}[/bold] -> state=[bold]{job.state.value}[/bold], result=[bold]{job.result.value}[/bold]",
            title="Result recorded",
            border_style="green",
        )
    )
    store.close()


# ------------------------------------------------------------------- resume
@app.command()
def resume(job_id: str = typer.Argument(..., help="Job ID.")) -> None:
    """Resume a paused/under-review job. Requires explicit human review acknowledgement."""
    _, store, service, _ = _ctx()
    job = store.get_job(job_id)
    if not job:
        store.close()
        _fail(f"Job '{job_id}' not found.")
    console.print(
        f"[yellow]Job {job.id} is '{job.state.value}'. A human must review it before resuming.[/yellow]"
    )
    ack = Confirm.ask("Have you reviewed this job and authorize resuming it?")
    try:
        job = service.resume_job(job_id, ack)
    except WorkerError as exc:
        store.close()
        _fail(str(exc))
    console.print(Panel(f"Job {job.id} resumed -> {job.state.value}", border_style="green"))
    store.close()


# ------------------------------------------------------------------- cancel
@app.command()
def cancel(job_id: str = typer.Argument(..., help="Job ID.")) -> None:
    """Cancel a job."""
    _, store, service, _ = _ctx()
    try:
        job = service.cancel_job(job_id)
    except WorkerError as exc:
        store.close()
        _fail(str(exc))
    console.print(Panel(f"Job {job.id} cancelled.", border_style="yellow"))
    store.close()


# -------------------------------------------------------------------- audit
@app.command()
def audit(job_id: str = typer.Argument(..., help="Job ID.")) -> None:
    """Show the redacted audit trail and state transitions for a job."""
    _, store, _, _ = _ctx()
    job = store.get_job(job_id)
    if not job:
        store.close()
        _fail(f"Job '{job_id}' not found.")

    trans = Table(title=f"State transitions — {job_id}", border_style="blue")
    trans.add_column("When")
    trans.add_column("From")
    trans.add_column("To")
    trans.add_column("Note")
    for t in store.list_transitions(job_id):
        trans.add_row(t.created_at.replace("T", " ")[:19], t.from_state or "-", t.to_state, t.note or "-")
    console.print(trans)

    events = Table(title=f"Audit events (redacted) — {job_id}", border_style="blue")
    events.add_column("When")
    events.add_column("Type")
    events.add_column("Message")
    for e in store.list_audit(job_id):
        events.add_row(e.created_at.replace("T", " ")[:19], e.event_type, e.message)
    console.print(events)
    store.close()


# ------------------------------------------------------------------ monitor
@app.command()
def monitor() -> None:
    """Live-updating dashboard of all jobs (Ctrl-C to exit)."""
    _, store, _, _ = _ctx()
    live_monitor(store, console)
    store.close()


# --------------------------------------------------------- simulate-failure
@app.command("simulate-failure")
def simulate_failure(
    job_id: str = typer.Argument(..., help="Job ID."),
    kind: FailureType = typer.Option(FailureType.TRANSIENT, "--kind", "-k", help="transient|permanent"),
    reason: str = typer.Option("network error", "--reason"),
) -> None:
    """Record a failure of the worker's own request (for testing retry policy)."""
    _, store, service, _ = _ctx()
    try:
        job = service.record_failure(job_id, kind, reason)
    except WorkerError as exc:
        store.close()
        _fail(str(exc))
    nxt = f", next_attempt={job.next_attempt_at}" if job.next_attempt_at else ""
    console.print(
        Panel(
            f"Job {job.id}: state={job.state.value}, attempts={job.attempts}/{job.max_attempts}{nxt}",
            title=f"{kind.value} failure recorded",
            border_style="magenta",
        )
    )
    store.close()


if __name__ == "__main__":
    app()
