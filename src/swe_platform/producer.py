"""Authenticated, pinned producer adapter for evaluator-owned v2 contracts."""

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, ValidationError
from pydantic import Field, model_validator

from .broker.host import AGENT_VERSION, HostGateway
from .candidates import Recipe, Workbench
from .credentials import GatewayProfile
from .io import Artifacts, atomic_write, canonical, digest, lock
from .models import StrictModel
from .policy import DevelopmentPolicy
from .telemetry import execution_trace
from .workflow import Workflow
from .workspace.snapshot import load_candidate


def validate_wire(name, value):
    schema = json.loads(
        (Path(__file__).parent / "contracts/v2" / (name + ".schema.json")).read_bytes()
    )
    try:
        Draft202012Validator(schema).validate(value)
        canonical(value)
    except (ValidationError, ValueError, TypeError):
        raise ValueError("Invalid evaluator " + name + " contract") from None
    return value


def implementation_identity():
    root = Path(__file__).parent
    return digest(
        canonical(
            {
                str(path.relative_to(root)): digest(path.read_bytes())
                for path in sorted(root.rglob("*"))
                if path.suffix in (".py", ".json", ".mjs") and "static" not in path.parts
            }
        )
    )


class EvaluatorProfile(StrictModel):
    schema_version: Literal["evaluator-profile/v1"] = "evaluator-profile/v1"
    base_url: str
    project: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$")
    evaluator_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    token_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,199}$")

    @model_validator(mode="after")
    def endpoint(self):
        url = urlsplit(self.base_url)
        if (
            not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError("Evaluator endpoint must be an origin without credentials")
        if url.scheme != "https" and not (
            url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost")
        ):
            raise ValueError("Evaluator transport requires HTTPS or explicit loopback HTTP")
        return self


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class EvaluatorClient:
    def __init__(self, profile):
        self.profile = profile
        self.token = os.environ.get(profile.token_env, "")
        if not self.token:
            raise ValueError("Evaluator credential is unavailable")
        self.opener = urllib.request.build_opener(NoRedirect())

    def call(self, path, body=None):
        allowed_get = re.fullmatch(
            r"/v1/(authority|(?:trial-tickets|execution-contracts|assessments|submissions)/[A-Za-z0-9_.-]+)",
            path,
        )
        if (
            body is None
            and not allowed_get
            or body is not None
            and path not in ("/v1/producer-artifacts", "/v1/submissions")
        ):
            raise ValueError("Operation is outside producer authority")
        raw = canonical(body) if body is not None else None
        if raw and len(raw) > 20 * 1024 * 1024:
            raise ValueError("Evaluator request exceeds the artifact limit")
        request = urllib.request.Request(
            self.profile.base_url.rstrip("/") + path,
            data=raw,
            headers={
                "Authorization": "Bearer " + self.token,
                "X-Project": self.profile.project,
                "Content-Type": "application/json",
            },
            method="POST" if raw is not None else "GET",
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read(2 * 1024 * 1024 + 1)
                if len(data) > 2 * 1024 * 1024:
                    raise ValueError("Evaluator response exceeds the limit")
                result = json.loads(data)
                canonical(result)
                return result
        except urllib.error.HTTPError as exc:
            raise ValueError(f"Evaluator request failed with HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise ValueError("Evaluator transport is unavailable") from None

    def verify_authority(self):
        identity = self.call("/v1/authority")
        if identity != {"contract_version": 2, "evaluator_sha256": self.profile.evaluator_sha256}:
            raise ValueError("Evaluator identity changed")


class ProducerRequest(StrictModel):
    schema_version: Literal["development-producer-request/v1"] = "development-producer-request/v1"
    key: str = Field(min_length=1, max_length=200)
    source: Path
    task: str = Field(min_length=1, max_length=12000)
    allowed_paths: list[str] = Field(min_length=1, max_length=100)
    recipe: Recipe
    gateway_profile: Path
    review_profile: Path | None = None
    image: str = Field(pattern=r"^(?:sha256:[a-f0-9]{64}|[^\s]+@sha256:[a-f0-9]{64})$")
    policy: DevelopmentPolicy = Field(default_factory=DevelopmentPolicy)
    producer_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    timeout: int = Field(default=600, ge=1, le=3600)
    max_requests: int = Field(default=30, ge=1, le=90)
    max_total_tokens: int = Field(default=250000, ge=1, le=1000000)
    max_repairs: int = Field(default=2, ge=0, le=2)

    def recipe_descriptor(self):
        coding = GatewayProfile.model_validate_json(self.gateway_profile.read_bytes())
        review = GatewayProfile.model_validate_json(
            (self.review_profile or self.gateway_profile).read_bytes()
        )
        result = {
            "schema_version": "agent-eval.recipe/v2",
            "name": self.policy.name,
            "producer_sha256": digest(
                canonical(
                    {
                        "revision": self.producer_revision,
                        "implementation_sha256": implementation_identity(),
                        "policy_sha256": self.policy.sha256,
                    }
                )
            ),
            "capability_sha256": digest(
                canonical(
                    {
                        "runtime": AGENT_VERSION,
                        "image": self.image,
                        "coding_tools": ["bash", "edit", "glob", "grep", "read", "write"],
                        "analysis_tools": ["glob", "grep", "read"],
                        "verification": self.recipe.model_dump(),
                        "allowed_paths": sorted(self.allowed_paths),
                    }
                )
            ),
            "model_configuration_sha256": digest(
                canonical({"coding": coding.model_dump(), "review": review.model_dump()})
            ),
            "budget": {
                "max_model_requests": self.max_requests,
                "max_total_tokens": self.max_total_tokens,
                "max_elapsed_seconds": self.timeout,
                "max_repairs": self.max_repairs,
            },
        }
        return validate_wire("Recipe", result)


IDENTITY = (
    "base_revision",
    "candidate_revision",
    "candidate_tree_sha256",
    "candidate_manifest_sha256",
)
BINDINGS = (
    "execution_id",
    "trial_ticket_sha256",
    "recipe_sha256",
    "suite_sha256",
    "policy_sha256",
    "evaluator_sha256",
)


def immutable(path, value):
    raw = canonical(value)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("Immutable producer record changed")
    if not path.exists():
        atomic_write(path, raw)


class Producer:
    def __init__(self, root, client):
        self.root, self.client = Path(root), client

    def directory(self, key):
        return Workflow(self.root).directory(key)

    def admit(self, request: ProducerRequest, ticket):
        validate_wire("TrialTicket", ticket)
        self.client.verify_authority()
        remote = self.client.call("/v1/trial-tickets/" + ticket["execution_id"])
        if (
            canonical(remote) != canonical(ticket)
            or ticket["evaluator_sha256"] != self.client.profile.evaluator_sha256
        ):
            raise ValueError("Trial ticket is not issued by the pinned evaluator")
        descriptor = request.recipe_descriptor()
        if digest(canonical(descriptor)) != ticket["recipe_sha256"]:
            raise ValueError("Producer configuration differs from the reserved recipe")
        directory = self.directory(request.key)
        with lock(directory / "producer.lock"):
            immutable(
                directory / "producer/admission.json",
                {
                    "ticket": ticket,
                    "recipe": descriptor,
                    "request_sha256": digest(canonical(request.model_dump(mode="json"))),
                },
            )
        return {
            "execution_id": ticket["execution_id"],
            "trial_ticket_sha256": digest(canonical(ticket)),
            "recipe_sha256": digest(canonical(descriptor)),
        }

    def run(self, request: ProducerRequest, ticket):
        self.admit(request, ticket)
        gateway = HostGateway(
            GatewayProfile.model_validate_json(request.gateway_profile.read_bytes())
        )
        reviewer = HostGateway(
            GatewayProfile.model_validate_json(
                (request.review_profile or request.gateway_profile).read_bytes()
            )
        )
        return Workflow(self.root, image=request.image).run(
            request.key,
            request.source,
            request.task,
            request.allowed_paths,
            request.recipe,
            gateway,
            review_gateway=reviewer,
            timeout=request.timeout,
            max_requests=request.max_requests,
            max_repairs=request.max_repairs,
            max_total_tokens=request.max_total_tokens,
            policy=request.policy,
            trial_ticket_sha256=digest(canonical(ticket)),
            execution_id=ticket["execution_id"],
            expected_base_revision=ticket["base_revision"],
        )

    def binding(self, key):
        directory = self.directory(key)
        admission = json.loads((directory / "producer/admission.json").read_bytes())
        ticket = admission["ticket"]
        record = json.loads((directory / "job.json").read_bytes())
        if not record["candidates"]:
            return {
                "execution_id": ticket["execution_id"],
                "trial_ticket_sha256": digest(canonical(ticket)),
                "producer_status": "cancelled" if record["state"] == "cancelled" else "failed",
                "candidate": None,
            }
        sha = record["candidates"][-1]
        bench = Workbench(directory / "evidence")
        candidate = load_candidate(bench.artifacts, sha)
        return {
            "schema_version": "agent-eval.execution/v2",
            **{
                field: ticket[field]
                for field in (
                    "execution_id",
                    "trial_identity",
                    "recipe_sha256",
                    "suite_sha256",
                    "policy_sha256",
                    "evaluator_sha256",
                )
            },
            "trial_ticket_sha256": digest(canonical(ticket)),
            "base_revision": candidate["base_revision"],
            "candidate_revision": None,
            "candidate_tree_sha256": candidate["tree_sha256"],
            "candidate_manifest_sha256": sha,
        }

    def submit(self, key):
        directory = self.directory(key)
        with lock(directory / "producer.lock", timeout=15):
            self.client.verify_authority()
            binding = self.binding(key)
            validate_wire("ExecutionContract", binding)
            contract = self.client.call("/v1/execution-contracts/" + binding["execution_id"])
            validate_wire("ExecutionContract", contract)
            if canonical(contract) != canonical(binding):
                raise ValueError("Issued execution contract does not bind the current candidate")
            record = json.loads((directory / "job.json").read_bytes())
            bench = Workbench(directory / "evidence")
            sha = binding["candidate_manifest_sha256"]
            artifacts = Artifacts(directory / "producer/artifacts")
            outbox = directory / "producer/submission.json"
            if outbox.exists():
                submission = json.loads(outbox.read_bytes())
                if any(submission[field] != binding[field] for field in (*IDENTITY, *BINDINGS)):
                    raise ValueError("Prepared submission belongs to a different candidate")
            else:
                candidate = load_candidate(bench.artifacts, sha)
                public = bench.inspect(sha)["evidence"]
                contents = {
                    "candidate": bench.artifacts.get(sha),
                    "patch": bench.artifacts.get(candidate["patch_sha256"]),
                    "trace": canonical(execution_trace(directory)),
                }
                if public.get("verification"):
                    contents["public-verification"] = canonical(public["verification"])
                if public.get("review"):
                    contents["review"] = canonical(public["review"])
                refs = []
                for role, raw in contents.items():
                    envelope = validate_wire(
                        "ArtifactEnvelope",
                        {
                            "schema_version": "agent-eval.artifact/v2",
                            "encoding": "base64",
                            "content_sha256": digest(raw),
                            "data": base64.b64encode(raw).decode(),
                        },
                    )
                    stored = artifacts.put(canonical(envelope))
                    refs.append({"role": role, "sha256": digest(raw), "storage_key": stored})
                usage = Workflow(self.root).inspect(key).get("accounting", {})
                submission = validate_wire(
                    "Submission",
                    {
                        "schema_version": "agent-eval.submission/v2",
                        **{field: binding[field] for field in (*IDENTITY, *BINDINGS)},
                        "execution_contract_sha256": digest(canonical(contract)),
                        "producer_run_id": directory.name,
                        "artifacts": refs,
                        "producer_status": "completed"
                        if record["state"] in ("ready_local", "verified_local")
                        else "cancelled"
                        if record["state"] == "cancelled"
                        else "failed",
                        "usage": {
                            "total_tokens": usage.get("total_tokens"),
                            "cost_usd": None,
                            "latency_ms": (record["events"][-1]["at"] - record["events"][0]["at"])
                            * 1000,
                            "provenance": "producer_reported",
                        },
                    },
                )
                immutable(outbox, submission)
            for ref in submission["artifacts"]:
                envelope = json.loads(artifacts.get(ref["storage_key"]))
                reply = self.client.call("/v1/producer-artifacts", envelope)
                if reply != {"sha256": ref["sha256"], "storage_key": ref["storage_key"]}:
                    raise ValueError("Evaluator changed an artifact identity")
            # The evaluator stores this execution ID immutably; identical retries are idempotent.
            receipt = self.client.call("/v1/submissions", submission)
            expected = {
                "execution_id": binding["execution_id"],
                "submission_sha256": digest(canonical(submission)),
                "status": "awaiting_independent_evaluation",
            }
            if receipt != expected:
                raise ValueError("Evaluator intake receipt does not bind the submission")
            immutable(directory / "producer/intake.json", receipt)
            return {
                "execution_id": binding["execution_id"],
                "submission_sha256": digest(canonical(submission)),
                "state": "awaiting_independent_evaluation",
                "receipt": receipt,
            }

    def assessment(self, key):
        directory = self.directory(key)
        with lock(directory / "producer.lock", timeout=15):
            self.client.verify_authority()
            submission = json.loads((directory / "producer/submission.json").read_bytes())
            assessment = self.client.call("/v1/assessments/" + submission["execution_id"])
            validate_wire("Assessment", assessment)
            if any(
                assessment[field] != submission[field]
                for field in (*IDENTITY, *BINDINGS, "execution_contract_sha256")
            ) or assessment["submission_sha256"] != digest(canonical(submission)):
                raise ValueError("Assessment does not match the submitted execution")
            binding = self.binding(key)
            if any(binding[field] != submission[field] for field in (*IDENTITY, *BINDINGS)):
                raise ValueError("Assessment belongs to a superseded candidate")
            if assessment["outcome"] == "pass" and (
                not assessment["checks"]
                or not all(check["passed"] for check in assessment["checks"])
            ):
                raise ValueError("Passing assessment contradicts its independent checks")
            immutable(directory / "producer/assessment.json", assessment)
            return {
                "state": "accepted" if assessment["outcome"] == "pass" else assessment["outcome"],
                "assessment_sha256": digest(canonical(assessment)),
                "assessment": assessment,
            }
