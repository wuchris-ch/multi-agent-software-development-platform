"""Local control-plane API over the same durable records used by the CLI."""

import json
import re
import secrets
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from . import __version__
from .candidates import Workbench
from .evidence import export_workflow
from .github import GitHub, RemoteError
from .github_publication import Publisher
from .improvement import PolicyRegistry
from .io import canonical, digest, lock
from .launch import Launcher, LaunchRequest
from .models import StrictModel
from .producer import EvaluatorClient, EvaluatorProfile, Producer, cached_assessment
from .telemetry import ModelLedger, execution_trace
from .workflow import Workflow
from .workspace.snapshot import load_candidate


class PrepareRequest(StrictModel):
    candidate: str = Field(pattern=r"^[a-f0-9]{64}$")
    repository: str = Field(pattern=r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
    branch: str = Field(min_length=1, max_length=200)
    base_branch: str | None = Field(default=None, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=16000)


class ApprovalRequest(StrictModel):
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Catalog:
    def __init__(self, root, github_factory=GitHub):
        self.root = Path(root)
        self.github_factory = github_factory

    def read(self, identifier):
        if not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise FileNotFoundError("Workflow not found")
        directory = self.root / "workflows" / identifier
        if directory.is_symlink():
            raise ValueError("Workflow directory must be local state")
        record = json.loads((directory / "job.json").read_bytes())
        key = record.get("key")
        if key is not None and digest(key.encode()) != identifier:
            raise ValueError("Workflow key does not match its saved identity")
        return directory, record

    def publisher(self, identifier):
        directory, _ = self.read(identifier)
        return Publisher(directory / "evidence", self.github_factory())

    def summary(self, identifier, record):
        events = record["events"]
        request = record["request"]
        title = request["task"].splitlines()[0].split(". ")[0].rstrip(".")
        if len(title) > 140:
            title = title[:137].rsplit(" ", 1)[0] + "..."
        return {
            "id": identifier,
            "key": record.get("key"),
            "title": title,
            "repository": Path(request["source"]).name,
            "state": record["state"],
            "created_at": events[0]["at"] if events else None,
            "updated_at": events[-1]["at"] if events else None,
            "request_budget": request["max_requests"],
            "requests_used_or_reserved": Workflow.spent(record),
            "repairs_used": max(0, len(record["candidates"]) - 1),
            "candidate_sha256": record["candidates"][-1] if record["candidates"] else None,
            "revision": digest(canonical(record)),
        }

    def list(self, limit=100):
        entries, unavailable = [], 0
        for path in (self.root / "workflows").glob("*/job.json"):
            try:
                _, record = self.read(path.parent.name)
                entries.append(self.summary(path.parent.name, record))
            except (OSError, ValueError, KeyError):
                unavailable += 1
        entries.sort(key=lambda item: item["updated_at"] or 0, reverse=True)
        return {"items": entries[:limit], "total": len(entries), "unavailable": unavailable}

    def candidate(self, directory, record, sha=None):
        sha = sha or (record["candidates"][-1] if record["candidates"] else None)
        if sha is None:
            return None
        if sha not in record["candidates"]:
            raise ValueError("Candidate does not belong to this workflow")
        bench = Workbench(directory / "evidence")
        inspected = bench.inspect(sha)
        manifest = load_candidate(bench.artifacts, sha)
        verification = inspected["evidence"].get("verification")
        return {
            "sha256": sha,
            "state": inspected["state"],
            "changed_paths": manifest["changed_paths"],
            "patch_sha256": manifest["patch_sha256"],
            "verification": verification,
            "check_output": bench.artifacts.get(verification["output_sha256"]).decode(
                errors="replace"
            )
            if verification
            else None,
            "review": inspected["evidence"].get("review", {}).get("verdict"),
        }

    def detail(self, identifier):
        directory, record = self.read(identifier)
        stages = []
        completed = {event["event"]: event["at"] for event in record["events"]}
        for name, step in record["steps"].items():
            result = step.get("result")
            stages.append(
                {
                    "name": name,
                    "state": "completed" if result is not None else "pending",
                    "requests": result.get("model_requests", 0)
                    if result is not None
                    else step["reserved_requests"],
                    "started_at": completed.get(name + ".intent"),
                    "finished_at": completed.get(name + ".completed"),
                }
            )
        publisher = self.publisher(identifier)
        publications = []
        for path in publisher.root.glob("*/plan.json"):
            publications.append(publisher.inspect(path.parent.name))
        publications.sort(key=lambda item: item["plan"]["created_at"], reverse=True)
        candidate = self.candidate(directory, record)
        state = record["state"]
        if state == "ready_local" and (candidate is None or candidate["state"] != "ready_local"):
            state = "needs_attention"
        return {
            **self.summary(identifier, record),
            "state": state,
            "task": record["request"]["task"],
            "base_revision": record["base"]["base_revision"],
            "deadline": record["deadline"],
            "active": record.get("active"),
            "max_repairs": record["request"]["max_repairs"],
            "stages": stages,
            "events": record["events"],
            "candidate": candidate,
            "candidates": record["candidates"],
            "publications": publications,
            "mode": record["request"].get("policy", {}).get("mode", "review"),
            "plan": record.get("plan"),
            "accounting": ModelLedger(
                directory,
                max_requests=record["request"]["max_requests"],
                max_tokens=record["request"]["max_total_tokens"],
                deadline=record["deadline"],
            ).summary()
            if (directory / "model-ledger.json").exists()
            else None,
            "acceptance": cached_assessment(directory),
            "evaluation_reserved": (directory / "producer/admission.json").exists(),
            "can_resume": (self.root / "launches" / identifier / "request.json").exists()
            and state == "needs_attention",
            "can_cancel": bool(record.get("key"))
            and state not in ("ready_local", "verified_local", "cancelled"),
        }


def create_app(
    root: Path,
    token: str,
    *,
    viewer_token=None,
    origin="http://127.0.0.1:8765",
    github_factory=GitHub,
    static_root=None,
    config=None,
    launcher=None,
):
    if (
        len(token) < 32
        or viewer_token is not None
        and (len(viewer_token) < 32 or viewer_token == token)
    ):
        raise ValueError("Use distinct random session tokens of at least 32 characters")
    parsed = urlparse(origin)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("The local control panel must bind to a loopback HTTP address")
    port = f":{parsed.port}" if parsed.port else ""
    hosts = {"127.0.0.1" + port, "localhost" + port}
    origins = {"http://" + host for host in hosts}
    catalog = Catalog(root, github_factory)
    launcher = launcher or Launcher(root, config)

    @asynccontextmanager
    async def lifespan(_app):
        with lock(root / "console.lock"):
            try:
                yield
            finally:
                launcher.close()

    app = FastAPI(
        title="Development control plane",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.catalog = catalog

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.headers.get("host") not in hosts:
            return JSONResponse({"detail": "Unrecognized local host"}, status_code=403)
        if request.headers.get("origin") and request.headers["origin"] not in origins:
            return JSONResponse({"detail": "Cross-origin access is not enabled"}, status_code=403)
        if request.scope["path"].startswith("/api/"):
            supplied = request.headers.get("authorization", "").encode()
            request.state.role = (
                "operator"
                if secrets.compare_digest(supplied, ("Bearer " + token).encode())
                else (
                    "viewer"
                    if viewer_token
                    and secrets.compare_digest(supplied, ("Bearer " + viewer_token).encode())
                    else None
                )
            )
            if request.state.role is None:
                return JSONResponse(
                    {"detail": "Open the session link printed by the server"}, status_code=401
                )
            if request.method not in ("GET", "HEAD") and request.state.role != "operator":
                return JSONResponse(
                    {"detail": "This action requires an operator session"}, status_code=403
                )
            if not request.headers.get("content-length", "0").isdigit():
                return JSONResponse({"detail": "Invalid request length"}, status_code=400)
            if int(request.headers.get("content-length", "0")) > 65536:
                return JSONResponse({"detail": "Request body exceeds 64 KiB"}, status_code=413)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            }
        )
        return response

    @app.exception_handler(FileNotFoundError)
    async def missing(_request, _exc):
        return JSONResponse(
            {"detail": "The requested workflow or artifact was not found"}, status_code=404
        )

    @app.exception_handler(BlockingIOError)
    async def busy(_request, _exc):
        return JSONResponse(
            {"detail": "This operation is already running; refresh its saved state"},
            status_code=409,
        )

    @app.exception_handler(ValueError)
    async def invalid(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _exc):
        return JSONResponse({"detail": "Invalid request parameters"}, status_code=422)

    @app.exception_handler(RemoteError)
    async def unavailable(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.get("/api/session")
    def session(request: Request):
        return {"role": request.state.role, "version": __version__}

    @app.get("/api/workflows")
    def workflows(limit: int = Query(100, ge=1, le=500)):
        return catalog.list(limit)

    @app.get("/api/projects")
    def projects():
        return {"items": launcher.projects(), "active_policy": PolicyRegistry(root).active()}

    @app.post("/api/launches")
    def launch(data: LaunchRequest):
        return launcher.submit(data)

    @app.get("/api/launches/{identifier}")
    def launch_status(identifier: str):
        return launcher.inspect(identifier)

    @app.post("/api/launches/{identifier}/resume")
    def resume(identifier: str):
        return launcher.resume(identifier)

    @app.get("/api/workflows/{identifier}/trace")
    def trace(identifier: str):
        directory, _ = catalog.read(identifier)
        return execution_trace(directory)

    def evaluator_for(identifier):
        _, record = catalog.read(identifier)
        projects = [
            project
            for project in launcher.config.projects
            if project.source.resolve() == Path(record["request"]["source"]).resolve()
            and project.evaluator_profile is not None
        ]
        if len(projects) != 1:
            raise ValueError("Configure one evaluator connection for this repository")
        client = EvaluatorClient(
            EvaluatorProfile.model_validate_json(projects[0].evaluator_profile.read_bytes())
        )
        return Producer(root, client), record["key"]

    @app.post("/api/workflows/{identifier}/acceptance/submit")
    def submit_acceptance(identifier: str):
        producer, key = evaluator_for(identifier)
        return producer.submit(key)

    @app.post("/api/workflows/{identifier}/acceptance/upload")
    def upload_acceptance(identifier: str):
        producer, key = evaluator_for(identifier)
        return producer.upload(key)

    @app.post("/api/workflows/{identifier}/acceptance/refresh")
    def refresh_acceptance(identifier: str):
        producer, key = evaluator_for(identifier)
        return producer.assessment(key)

    @app.get("/api/policies")
    def policies():
        registry = PolicyRegistry(root)
        comparisons = [
            json.loads(path.read_bytes())
            for path in sorted((registry.root / "gates").glob("*/*.json"))
            if path.name in ("development.json", "held_out.json")
        ]
        return {"active": registry.active(), "comparisons": comparisons}

    @app.get("/api/workflows/{identifier}")
    def workflow(identifier: str):
        return catalog.detail(identifier)

    @app.get("/api/workflows/{identifier}/events")
    def events(identifier: str, after: int = Query(0, ge=0)):
        _, record = catalog.read(identifier)
        entries = [event for event in record["events"] if event["seq"] > after][:500]
        return {"events": entries, "cursor": entries[-1]["seq"] if entries else after}

    @app.get("/api/workflows/{identifier}/bundle")
    def bundle(identifier: str):
        directory, _ = catalog.read(identifier)
        raw = export_workflow(directory)
        sha = digest(raw)
        return Response(
            raw,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="development-evidence-{sha[:12]}.json"',
                "X-Content-SHA256": sha,
            },
        )

    @app.get("/api/workflows/{identifier}/diff")
    def diff(identifier: str, candidate: str | None = None):
        directory, record = catalog.read(identifier)
        inspected = catalog.candidate(directory, record, candidate)
        if inspected is None:
            raise HTTPException(409, "This workflow has not produced a candidate yet")
        bench = Workbench(directory / "evidence")
        return {
            "candidate": inspected["sha256"],
            "patch_sha256": inspected["patch_sha256"],
            "diff": bench.artifacts.get(inspected["patch_sha256"]).decode(errors="replace"),
        }

    @app.post("/api/workflows/{identifier}/cancel")
    def cancel(identifier: str):
        _, record = catalog.read(identifier)
        if not record.get("key"):
            raise HTTPException(409, "Resume this workflow once from the CLI to register its key")
        return Workflow(root).cancel(record["key"])

    @app.post("/api/workflows/{identifier}/publications")
    def prepare(identifier: str, data: PrepareRequest):
        _, record = catalog.read(identifier)
        if not record["candidates"] or record["candidates"][-1] != data.candidate:
            raise HTTPException(
                409, "The current candidate changed; refresh before preparing publication"
            )
        return catalog.publisher(identifier).prepare(
            data.candidate,
            data.repository,
            branch=data.branch,
            base_branch=data.base_branch,
            title=data.title,
            body=data.body,
        )

    @app.post("/api/workflows/{identifier}/publications/{plan}/publish")
    def publish(identifier: str, plan: str, data: ApprovalRequest):
        if data.plan_sha256 != plan:
            raise HTTPException(409, "Approval belongs to a different publication plan")
        return catalog.publisher(identifier).publish(plan)

    @app.post("/api/workflows/{identifier}/publications/{plan}/reconcile")
    def reconcile(identifier: str, plan: str):
        return catalog.publisher(identifier).publish(plan, reconcile_only=True)

    static = Path(static_root) if static_root else Path(str(files("swe_platform") / "static"))
    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/")
    def index():
        if not (static / "index.html").is_file():
            return JSONResponse(
                {"detail": "Build the control panel with npm --prefix web run build"},
                status_code=503,
            )
        return FileResponse(static / "index.html")

    return app
