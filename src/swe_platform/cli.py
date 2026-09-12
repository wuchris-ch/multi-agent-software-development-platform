import json
import platform
import secrets
import shutil
import subprocess
import uuid
from pathlib import Path

import typer

from . import __version__
from .adapters.flue import capabilities
from .broker.host import AgentRun, HostGateway
from .candidates import Recipe, Workbench
from .coding import CandidateCoding
from .credentials import GatewayProfile
from .evidence import verify_bundle, write_bundle
from .github_publication import Publisher
from .improvement import PolicyRegistry, PromotionGate
from .io import atomic_write, canonical, lock
from .launch import ConsoleConfig
from .models import Submission
from .policy import DevelopmentPolicy
from .producer import EvaluatorClient, EvaluatorProfile, Producer, ProducerRequest
from .service import request
from .service import serve as run_service
from .store import Store
from .telemetry import execution_trace
from .workflow import Workflow

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Durable local software development jobs",
)
candidate_app = typer.Typer(
    no_args_is_help=True, help="Import and inspect local repository patches"
)
app.add_typer(candidate_app, name="candidate")
workflow_app = typer.Typer(
    no_args_is_help=True, help="Orchestrate Flue coding, checks, review, and repairs"
)
app.add_typer(workflow_app, name="workflow")
publication_app = typer.Typer(
    no_args_is_help=True, help="Prepare, publish, and reconcile draft PRs"
)
app.add_typer(publication_app, name="publication")
evidence_app = typer.Typer(
    no_args_is_help=True, help="Verify portable development evidence offline"
)
app.add_typer(evidence_app, name="evidence")
producer_app = typer.Typer(
    no_args_is_help=True, help="Run reserved evaluation trials and submit exact candidates"
)
app.add_typer(producer_app, name="producer")
improvement_app = typer.Typer(
    no_args_is_help=True, help="Propose, evaluate and roll out versioned production policies"
)
app.add_typer(improvement_app, name="improvement")
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
    checks["agent_runtime"] = capabilities()
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


@candidate_app.command("code")
def code_candidate(
    ctx: typer.Context,
    source: Path,
    recipe: Path,
    gateway_profile: Path,
    task: str = typer.Option(...),
    allow: list[str] = typer.Option(...),
    key: str = typer.Option(...),
    image: str | None = None,
    timeout: float = 180,
    max_requests: int = 12,
):
    """Run one isolated coding worker and seal its local candidate under a durable key."""
    spec = Recipe.model_validate_json(recipe.read_bytes())
    profile = GatewayProfile.model_validate_json(gateway_profile.read_bytes())
    emit(
        CandidateCoding(ctx.obj, image=image).run(
            key,
            source.resolve(),
            task,
            allow,
            spec,
            HostGateway(profile),
            timeout=timeout,
            max_requests=max_requests,
        )
    )


@candidate_app.command("stop-coding")
def stop_coding(ctx: typer.Context, key: str):
    """Stop a live or interrupted coding attempt and confirm its container is stopped."""
    emit(AgentRun(ctx.obj / "coding").cancel(key))


@candidate_app.command("verify")
def verify_candidate(ctx: typer.Context, sha: str):
    """Run the pinned public recipe on an immutable snapshot without network or credentials."""
    emit(Workbench(ctx.obj).verify(sha))


@candidate_app.command("review")
def review_candidate(ctx: typer.Context, sha: str, gateway_profile: Path, image: str | None = None):
    """Run the built-in Flue reviewer against the exact candidate diff."""
    profile = GatewayProfile.model_validate_json(gateway_profile.read_bytes())
    emit(Workbench(ctx.obj).review_agent(sha, HostGateway(profile), image=image))


@candidate_app.command("repair")
def repair_candidate(ctx: typer.Context, sha: str, patch: Path):
    """Import a replacement patch against the original base, with a two-repair limit."""
    emit(Workbench(ctx.obj).repair(sha, patch.read_bytes()))


@candidate_app.command("inspect")
def inspect_candidate(ctx: typer.Context, sha: str):
    """Validate evidence against the exact candidate and display the local patch path."""
    emit(Workbench(ctx.obj).inspect(sha))


@workflow_app.command("run")
def run_workflow(
    ctx: typer.Context,
    source: Path,
    recipe: Path,
    gateway_profile: Path,
    task: str = typer.Option(...),
    allow: list[str] = typer.Option(...),
    key: str = typer.Option(...),
    image: str | None = None,
    review_profile: Path | None = None,
    timeout: float = 600,
    max_requests: int = 30,
    max_repairs: int = 2,
    max_total_tokens: int | None = None,
    mode: str | None = None,
    policy: Path | None = None,
):
    """Run or resume an entire development task with Flue agents."""
    if mode and policy:
        raise typer.BadParameter("Select a mode or a policy file")
    selected = (
        DevelopmentPolicy.model_validate_json(policy.read_bytes())
        if policy
        else DevelopmentPolicy.preset(mode)
        if mode
        else None
    )
    gateway = HostGateway(GatewayProfile.model_validate_json(gateway_profile.read_bytes()))
    reviewer = (
        HostGateway(GatewayProfile.model_validate_json(review_profile.read_bytes()))
        if review_profile
        else gateway
    )
    emit(
        Workflow(ctx.obj, image=image).run(
            key,
            source.resolve(),
            task,
            allow,
            Recipe.model_validate_json(recipe.read_bytes()),
            gateway,
            review_gateway=reviewer,
            timeout=timeout,
            max_requests=max_requests,
            max_repairs=max_repairs,
            max_total_tokens=max_total_tokens,
            policy=selected,
        )
    )


@workflow_app.command("inspect")
def inspect_workflow(ctx: typer.Context, key: str):
    """Show current candidate, exact evidence, stage history, and shared budget."""
    emit(Workflow(ctx.obj).inspect(key))


@workflow_app.command("cancel")
def cancel_workflow(ctx: typer.Context, key: str):
    """Stop the active stage and persist a cancellation request."""
    emit(Workflow(ctx.obj).cancel(key))


@workflow_app.command("export")
def export_evidence(ctx: typer.Context, key: str, destination: Path):
    """Export the current candidate, recipe, checks, review, and structured stage history."""
    emit(write_bundle(Workflow(ctx.obj).directory(key), destination))


@workflow_app.command("trace")
def export_trace(ctx: typer.Context, key: str, destination: Path):
    """Export stage, model, tool, repair and publication observations without prompt text."""
    raw = canonical(execution_trace(Workflow(ctx.obj).directory(key)))
    atomic_write(destination, raw)
    emit({"path": str(destination.resolve()), "bytes": len(raw)})


@workflow_app.command("policy")
def show_policy(mode: str = "review"):
    """Print an immutable production policy for configuration and matched comparisons."""
    selected = DevelopmentPolicy.preset(mode)
    emit({"policy": selected.model_dump(), "sha256": selected.sha256})


def evaluator(path):
    return EvaluatorClient(EvaluatorProfile.model_validate_json(path.read_bytes()))


@improvement_app.command("initialize")
def initialize_policy(ctx: typer.Context, mode: str = "review"):
    emit(PolicyRegistry(ctx.obj).initialize(DevelopmentPolicy.preset(mode)))


@improvement_app.command("register")
def register_policy(ctx: typer.Context, policy: Path):
    emit(
        {
            "policy_sha256": PolicyRegistry(ctx.obj).register(
                DevelopmentPolicy.model_validate_json(policy.read_bytes())
            )
        }
    )


@improvement_app.command("propose")
def propose_policy(ctx: typer.Context, parent: str):
    emit(PolicyRegistry(ctx.obj).propose(parent))


@improvement_app.command("freeze")
def freeze_gate(ctx: typer.Context, gate: Path):
    emit(PolicyRegistry(ctx.obj).freeze(PromotionGate.model_validate_json(gate.read_bytes())))


@improvement_app.command("evaluate")
def evaluate_policy(ctx: typer.Context, gate: str, split: str):
    emit(PolicyRegistry(ctx.obj).evaluate(gate, split))


@improvement_app.command("promote")
def promote_policy(ctx: typer.Context, gate: str, expected: str, generation: int):
    emit(PolicyRegistry(ctx.obj).promote(gate, expected, generation))


@improvement_app.command("rollback")
def rollback_policy(ctx: typer.Context, target: str, expected: str, generation: int):
    emit(PolicyRegistry(ctx.obj).rollback(target, expected, generation))


@improvement_app.command("active")
def active_policy(ctx: typer.Context):
    emit(PolicyRegistry(ctx.obj).active())


@producer_app.command("recipe")
def producer_recipe(request_file: Path):
    emit(ProducerRequest.model_validate_json(request_file.read_bytes()).recipe_descriptor())


@producer_app.command("run")
def producer_run(ctx: typer.Context, request_file: Path, ticket: Path, profile: Path):
    """Validate an evaluator-issued reservation before production dispatch."""
    request = ProducerRequest.model_validate_json(request_file.read_bytes())
    emit(Producer(ctx.obj, evaluator(profile)).run(request, json.loads(ticket.read_bytes())))


@producer_app.command("binding")
def producer_binding(ctx: typer.Context, key: str):
    """Describe sealed content for the evaluator to issue its final execution contract."""
    emit(Producer(ctx.obj, None).binding(key))


@producer_app.command("submit")
def producer_submit(ctx: typer.Context, key: str, profile: Path):
    emit(Producer(ctx.obj, evaluator(profile)).submit(key))


@producer_app.command("upload")
def producer_upload(ctx: typer.Context, key: str, profile: Path):
    """Upload exact evidence before the evaluator issues its candidate-bound contract."""
    emit(Producer(ctx.obj, evaluator(profile)).upload(key))


@producer_app.command("assessment")
def producer_assessment(ctx: typer.Context, key: str, profile: Path):
    emit(Producer(ctx.obj, evaluator(profile)).assessment(key))


@evidence_app.command("verify")
def verify_evidence(bundle: Path, expected: str | None = None):
    """Validate an evidence bundle without a provider, Docker, or GitHub connection."""
    emit(verify_bundle(bundle.read_bytes(), expected_sha256=expected))


def publisher(root, workflow):
    return Publisher(Workflow(root).directory(workflow) / "evidence" if workflow else root)


@publication_app.command("prepare")
def prepare_publication(
    ctx: typer.Context,
    candidate: str,
    repository: str = typer.Option(...),
    branch: str = typer.Option(...),
    title: str = typer.Option(...),
    body_file: Path = typer.Option(...),
    base_branch: str | None = None,
    workflow: str | None = None,
):
    """Prepare an exact publication plan using read-only GitHub checks."""
    emit(
        publisher(ctx.obj, workflow).prepare(
            candidate,
            repository,
            branch=branch,
            title=title,
            body=body_file.read_text(),
            base_branch=base_branch,
        )
    )


@publication_app.command("inspect")
def inspect_publication(ctx: typer.Context, plan: str, workflow: str | None = None):
    """Show the approved content, remote receipts, and exact-head check results."""
    emit(publisher(ctx.obj, workflow).inspect(plan))


@publication_app.command("publish")
def publish_candidate(ctx: typer.Context, plan: str, workflow: str | None = None):
    """Approve this exact plan and publish its candidate as a draft pull request."""
    emit(publisher(ctx.obj, workflow).publish(plan))


@publication_app.command("reconcile")
def reconcile_publication(ctx: typer.Context, plan: str, workflow: str | None = None):
    """Read GitHub to reconcile saved writes and refresh checks without creating remote objects."""
    emit(publisher(ctx.obj, workflow).publish(plan, reconcile_only=True))


@app.command("ui")
def control_panel(
    ctx: typer.Context,
    port: int = typer.Option(8765, min=1024, max=65535),
    config: Path | None = None,
):
    """Open the local control panel for runs, diffs, review, and draft PR publication."""
    import uvicorn

    from .console import create_app

    token, viewer = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    origin = f"http://127.0.0.1:{port}"
    typer.echo(f"Operator session: {origin}/#token={token}")
    typer.echo(f"Viewer session: {origin}/#token={viewer}")
    uvicorn.run(
        create_app(
            ctx.obj,
            token,
            viewer_token=viewer,
            origin=origin,
            config=ConsoleConfig.model_validate_json(config.read_bytes()) if config else None,
        ),
        host="127.0.0.1",
        port=port,
        access_log=False,
    )
