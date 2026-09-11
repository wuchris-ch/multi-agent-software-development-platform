import json
import platform
import shutil
import subprocess
import uuid
from pathlib import Path

import typer

from . import __version__
from .candidates import Recipe, Workbench
from .credentials import GatewayProfile
from .io import lock
from .models import Submission
from .service import request
from .service import serve as run_service
from .store import Store

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Durable local software engineering jobs",
)
candidate_app = typer.Typer(
    no_args_is_help=True, help="Import and inspect local repository patches"
)
app.add_typer(candidate_app, name="candidate")
DEFAULT_STATE = Path.home() / "Library/Application Support/SWEPlatform"


@app.callback()
def main(
    ctx: typer.Context, state: Path = typer.Option(DEFAULT_STATE, envvar="SWE_PLATFORM_STATE")
):
    ctx.obj = state.expanduser().resolve()


def emit(value):
    typer.echo(json.dumps(value, indent=2))


@app.command()
def init(ctx: typer.Context):
    """Initialize private state. Never launches workers or a watcher."""
    root = ctx.obj
    try:
        with lock(root / "service.lock"):
            Store(root).close()
    except BlockingIOError:
        raise typer.BadParameter("Service already owns this state") from None
    emit({"state_directory": str(root), "version": __version__})


@app.command()
def doctor():
    """Report prerequisites without reading or printing secret values."""
    checks = {"python": platform.python_version(), "platform": __version__}
    for tool, args in {
        "git": ["--version"],
        "docker": ["version", "--format", "{{.Server.Version}}"],
        "codex": ["--version"],
        "node": ["--version"],
    }.items():
        binary = shutil.which(tool)
        try:
            result = (
                subprocess.run([binary, *args], capture_output=True, text=True, timeout=8)
                if binary
                else None
            )
            checks[tool] = (
                result.stdout.strip() if result and result.returncode == 0 else "unavailable"
            )
        except subprocess.TimeoutExpired:
            checks[tool] = "unavailable"
    checks["coding_adapter"] = "disabled: credential broker and network profile not verified"
    checks["scripted_fixture"] = "available; fixed trusted code only"
    emit(checks)


@app.command()
def serve(ctx: typer.Context, fault: str | None = typer.Option(None, hidden=True)):
    """Run one coordinator. Restart adopts existing executions."""
    try:
        run_service(ctx.obj, fault)
    except BlockingIOError:
        raise typer.BadParameter("Another service owns this state directory") from None


@app.command()
def submit(
    ctx: typer.Context,
    key: str = typer.Option(None),
    task: str = "Fix fixture addition",
    delay: float = 0.1,
    behavior: str = "fix",
    deadline: float = 60,
    adapter: str = "scripted",
):
    """Submit a credential-free fixture job; task text is descriptive only."""
    spec = Submission(
        key=key or str(uuid.uuid4()),
        task=task,
        delay_seconds=delay,
        behavior=behavior,
        deadline_seconds=deadline,
        adapter=adapter,
    )
    emit(request(ctx.obj, "submit", submission=spec.model_dump()))


@app.command()
def status(ctx: typer.Context, job_id: str | None = None):
    emit(request(ctx.obj, "status", job_id=job_id))


@app.command()
def inspect(ctx: typer.Context, job_id: str):
    emit(request(ctx.obj, "inspect", job_id=job_id))


@app.command()
def cancel(ctx: typer.Context, job_id: str):
    """Persist cancellation; status confirms when execution has stopped."""
    emit(request(ctx.obj, "cancel", job_id=job_id))


@candidate_app.command("import")
def import_candidate(
    ctx: typer.Context,
    source: Path,
    patch: Path,
    recipe: Path,
    allow: list[str] = typer.Option(...),
):
    """Import a bounded local patch from a clean repository and pin its trusted recipe."""
    spec = Recipe.model_validate_json(recipe.read_bytes())
    emit(Workbench(ctx.obj).intake(source.resolve(), patch.read_bytes(), allow, spec))


@candidate_app.command("verify")
def verify_candidate(ctx: typer.Context, sha: str):
    """Run the pinned public recipe on an immutable snapshot without network or credentials."""
    emit(Workbench(ctx.obj).verify(sha))


@candidate_app.command("review")
def review_candidate(ctx: typer.Context, sha: str, flue_cli: Path, gateway_profile: Path):
    """Obtain a fresh Flue verdict using a private Keychain-backed gateway profile."""
    profile = GatewayProfile.model_validate_json(gateway_profile.read_bytes())
    node = shutil.which("node")
    if node is None:
        raise typer.BadParameter("Node is unavailable")
    emit(Workbench(ctx.obj).review(sha, flue_cli, profile, node))


@candidate_app.command("repair")
def repair_candidate(ctx: typer.Context, sha: str, patch: Path):
    """Import a replacement patch against the original base, with a two-repair limit."""
    emit(Workbench(ctx.obj).repair(sha, patch.read_bytes()))


@candidate_app.command("inspect")
def inspect_candidate(ctx: typer.Context, sha: str):
    """Validate evidence against the exact candidate and display the local patch path."""
    emit(Workbench(ctx.obj).inspect(sha))
