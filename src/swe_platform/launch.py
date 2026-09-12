"""Bounded console admission over the existing workflow owner, without a second queue."""

import json
import threading
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .broker.host import HostGateway
from .candidates import Recipe
from .credentials import GatewayProfile
from .improvement import PolicyRegistry
from .io import atomic_write, canonical, digest, lock
from .models import StrictModel
from .policy import DevelopmentPolicy
from .producer import EvaluatorClient, EvaluatorProfile, Producer, ProducerRequest
from .workflow import Workflow


class Project(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    title: str = Field(min_length=1, max_length=100)
    source: Path
    recipe: Recipe
    gateway_profile: Path
    review_profile: Path | None = None
    evaluator_profile: Path | None = None
    image: str = Field(pattern=r"^(?:sha256:[a-f0-9]{64}|[^\s]+@sha256:[a-f0-9]{64})$")
    allowed_paths: list[str] = Field(min_length=1, max_length=100)
    max_requests: int = Field(default=30, ge=1, le=90)
    max_total_tokens: int = Field(default=250000, ge=1, le=1000000)
    timeout: int = Field(default=600, ge=1, le=3600)
    max_repairs: int = Field(default=2, ge=0, le=2)


class ConsoleConfig(StrictModel):
    schema_version: Literal["development-console/v1"] = "development-console/v1"
    projects: list[Project] = Field(max_length=30)

    @model_validator(mode="after")
    def unique(self):
        if len({project.id for project in self.projects}) != len(self.projects):
            raise ValueError("Console project IDs must be unique")
        return self


class LaunchRequest(StrictModel):
    key: str = Field(min_length=1, max_length=200)
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    task: str = Field(min_length=1, max_length=12000)
    mode: Literal["single", "review", "selective", "active"] = "review"
    execution_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


class Launcher:
    def __init__(self, root, config=None, execute=None):
        self.root = Path(root)
        self.config = config or ConsoleConfig(projects=[])
        self.execute = execute or self._execute
        self.guard = threading.Lock()
        self.thread = self.key = None
        self.closing = False

    def projects(self):
        return [
            {
                "id": project.id,
                "title": project.title,
                "allowed_paths": project.allowed_paths,
                "max_requests": project.max_requests,
                "max_total_tokens": project.max_total_tokens,
                "timeout": project.timeout,
                "requires_ticket": project.evaluator_profile is not None,
            }
            for project in self.config.projects
        ]

    def project(self, identifier):
        return next((project for project in self.config.projects if project.id == identifier), None)

    def directory(self, identifier):
        if len(identifier) != 64 or any(c not in "0123456789abcdef" for c in identifier):
            raise ValueError("Invalid launch identity")
        return self.root / "launches" / identifier

    def inspect(self, identifier):
        directory = self.directory(identifier)
        record = json.loads((directory / "request.json").read_bytes())
        job = self.root / "workflows" / identifier / "job.json"
        if job.exists():
            state = json.loads(job.read_bytes())["state"]
        else:
            receipt = directory / "result.json"
            state = json.loads(receipt.read_bytes())["state"] if receipt.exists() else "preparing"
        return {
            "id": identifier,
            "key": record["request"]["key"],
            "state": state,
            "workflow_id": identifier if job.exists() else None,
        }

    def submit(self, request, *, resume=False):
        project = self.project(request.project_id)
        if project is None:
            raise ValueError("Select a configured project")
        identifier = digest(request.key.encode())
        directory = self.directory(identifier)
        existing = directory / "request.json"
        if request.mode == "active":
            registry = PolicyRegistry(self.root)
            previous = json.loads(existing.read_bytes()) if existing.exists() else None
            active = registry.active()
            if previous:
                policy = DevelopmentPolicy.model_validate(previous["policy"])
            elif active:
                policy = registry.policy(active["policy_sha256"])
            else:
                raise ValueError("Initialize an active production policy before selecting it")
        else:
            policy = DevelopmentPolicy.preset(request.mode)
        envelope = {
            "request": request.model_dump(),
            "project_sha256": digest(canonical(project.model_dump(mode="json"))),
            "policy": policy.model_dump(),
        }
        with self.guard, lock(directory / "control.lock", timeout=15):
            existing = directory / "request.json"
            if existing.exists():
                if existing.read_bytes() != canonical(envelope):
                    raise ValueError(
                        "Launch key already has another request or project configuration"
                    )
                if not resume:
                    return self.inspect(identifier)
            if self.closing or self.thread and self.thread.is_alive():
                raise ValueError("A console-launched workflow is already active")
            # No in-memory waiting queue: persist one admitted request, then dispatch its owner.
            atomic_write(existing, canonical(envelope))
            self.key = request.key

            def run():
                try:
                    result = self.execute(project, request)
                    receipt = {"state": result["state"]}
                except BaseException as exc:
                    receipt = {"state": "needs_attention", "error_class": type(exc).__name__}
                atomic_write(directory / "result.json", canonical(receipt))

            self.thread = threading.Thread(target=run, daemon=True, name="development-workflow")
            self.thread.start()
            return {"id": identifier, "key": request.key, "state": "preparing", "workflow_id": None}

    def resume(self, identifier):
        envelope = json.loads((self.directory(identifier) / "request.json").read_bytes())
        return self.submit(LaunchRequest.model_validate(envelope["request"]), resume=True)

    def _execute(self, project, request):
        envelope = json.loads(
            (self.directory(digest(request.key.encode())) / "request.json").read_bytes()
        )
        selected_policy = DevelopmentPolicy.model_validate(envelope["policy"])
        if project.evaluator_profile is not None:
            if request.execution_id is None:
                raise ValueError("This project requires a reserved evaluation ticket")
            client = EvaluatorClient(
                EvaluatorProfile.model_validate_json(project.evaluator_profile.read_bytes())
            )
            ticket = client.call("/v1/trial-tickets/" + request.execution_id)
            spec = ProducerRequest(
                key=request.key,
                source=project.source,
                task=request.task,
                allowed_paths=project.allowed_paths,
                recipe=project.recipe,
                gateway_profile=project.gateway_profile,
                review_profile=project.review_profile,
                image=project.image,
                policy=selected_policy,
                timeout=project.timeout,
                max_requests=project.max_requests,
                max_total_tokens=project.max_total_tokens,
                max_repairs=project.max_repairs,
            )
            return Producer(self.root, client).run(spec, ticket)
        gateway = HostGateway(
            GatewayProfile.model_validate_json(project.gateway_profile.read_bytes())
        )
        reviewer = HostGateway(
            GatewayProfile.model_validate_json(
                (project.review_profile or project.gateway_profile).read_bytes()
            )
        )
        return Workflow(self.root, image=project.image).run(
            request.key,
            project.source,
            request.task,
            project.allowed_paths,
            project.recipe,
            gateway,
            review_gateway=reviewer,
            timeout=project.timeout,
            max_requests=project.max_requests,
            max_total_tokens=project.max_total_tokens,
            max_repairs=project.max_repairs,
            policy=selected_policy,
        )

    def close(self):
        with self.guard:
            self.closing = True
            thread, key = self.thread, self.key
        if thread and thread.is_alive():
            directory = Workflow(self.root).directory(key)
            atomic_write(directory / "cancel", b"console shutting down\n")
            if (directory / "job.json").exists():
                try:
                    Workflow(self.root).cancel(key)
                except Exception:
                    pass  # The existing workflow retains an unconfirmed execution for inspection.
            thread.join(timeout=15)
