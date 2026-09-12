"""Portable, self-contained evidence with offline artifact and policy validation."""

import base64
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .candidates import Recipe, Workbench
from .io import atomic_write, canonical, digest
from .models import StrictModel
from .review.flue import validate
from .telemetry import execution_trace
from .workspace.snapshot import MAX_BYTES, MAX_FILES, load_candidate

MAX_BUNDLE_BYTES = 32 * 1024 * 1024


class Verification(StrictModel):
    candidate: str = Field(pattern=r"^[a-f0-9]{64}$")
    recipe_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    passed: bool = Field(strict=True)
    exit_code: int = Field(strict=True)
    reason: str | None
    output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Usage(StrictModel):
    input_tokens: int = Field(ge=0, strict=True)
    output_tokens: int = Field(ge=0, strict=True)
    cached_input_tokens: int = Field(ge=0, strict=True)
    cache_write_input_tokens: int = Field(ge=0, strict=True)
    total_tokens: int = Field(ge=0, strict=True)


class Stage(StrictModel):
    name: str = Field(max_length=100)
    completed: bool
    reserved_requests: int = Field(ge=0, le=90)
    model_requests: int | None = Field(ge=0, le=90)
    usage: Usage | None


class Event(StrictModel):
    seq: int = Field(gt=0)
    at: float
    event: str = Field(max_length=200)


class RunMetadata(StrictModel):
    workflow_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: str = Field(max_length=100)
    runtime: str | None = Field(max_length=100)
    agent_image: str | None = Field(max_length=300)
    request_budget: int = Field(gt=0, le=90)
    repair_budget: int = Field(ge=0, le=2)
    repairs_used: int = Field(ge=0, le=2)
    stages: list[Stage] = Field(max_length=20)
    events: list[Event] = Field(max_length=10000)


class Bundle(StrictModel):
    schema_version: Literal["development-evidence-bundle/v1"] = "development-evidence-bundle/v1"
    candidate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    recipe: Recipe
    allowed_paths: list[str] = Field(max_length=MAX_FILES)
    verification: Verification | None
    review: dict | None
    # Explicitly selected metadata never includes prompts, profiles, source paths, or worker files.
    run: RunMetadata
    trace_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    artifacts: dict[str, str] = Field(max_length=4)


class BundleArtifacts:
    def __init__(self, entries):
        self.entries = entries

    def get(self, sha):
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("Invalid evidence artifact digest")
        try:
            data = base64.b64decode(self.entries[sha], validate=True)
        except (ValueError, KeyError):
            raise ValueError("Missing or malformed evidence artifact") from None
        if digest(data) != sha:
            raise ValueError("Evidence artifact digest mismatch")
        return data


def export_workflow(directory: Path) -> bytes:
    record = json.loads((directory / "job.json").read_bytes())
    if not record["candidates"]:
        raise ValueError("Workflow has not produced a candidate")
    sha = record["candidates"][-1]
    bench = Workbench(directory / "evidence")
    inspected = bench.inspect(sha)
    candidate = load_candidate(bench.artifacts, sha)
    policy = bench.policy(sha)
    evidence = inspected["evidence"]
    verification = evidence.get("verification")
    artifacts = {sha, candidate["patch_sha256"]}
    if verification:
        artifacts.add(verification["output_sha256"])
    request = record["request"]
    run = {
        "workflow_id": directory.name,
        "state": record["state"],
        "runtime": request.get("runtime"),
        "agent_image": request.get("image"),
        "request_budget": request["max_requests"],
        "repair_budget": request["max_repairs"],
        "repairs_used": max(0, len(record["candidates"]) - 1),
        "stages": [
            {
                "name": name,
                "completed": "result" in step,
                "reserved_requests": step["reserved_requests"],
                "model_requests": step.get("result", {}).get("model_requests"),
                "usage": {field: usage[field] for field in Usage.model_fields}
                if (usage := step.get("result", {}).get("usage")) is not None
                else None,
            }
            for name, step in record["steps"].items()
        ],
        "events": [
            {key: event[key] for key in ("seq", "at", "event")} for event in record["events"]
        ],
    }
    trace = canonical(execution_trace(directory))
    trace_sha = digest(trace)
    bundle = Bundle(
        candidate_sha256=sha,
        base_revision=candidate["base_revision"],
        recipe=policy["recipe"],
        allowed_paths=policy["allowed_paths"],
        verification=verification,
        review=evidence.get("review", {}).get("verdict"),
        run=run,
        trace_sha256=trace_sha,
        artifacts={
            trace_sha: base64.b64encode(trace).decode(),
            **{
                key: base64.b64encode(bench.artifacts.get(key)).decode()
                for key in sorted(artifacts)
            },
        },
    )
    raw = canonical(bundle.model_dump())
    verify_bundle(raw)
    return raw


def verify_bundle(raw: bytes, *, expected_sha256: str | None = None):
    if not 0 < len(raw) <= MAX_BUNDLE_BYTES:
        raise ValueError("Evidence bundle must be between 1 byte and 32 MiB")
    sha = digest(raw)
    if expected_sha256 is not None and sha != expected_sha256:
        raise ValueError("Evidence bundle does not match the expected digest")
    bundle = Bundle.model_validate_json(raw)
    artifacts = BundleArtifacts(bundle.artifacts)
    candidate = load_candidate(artifacts, bundle.candidate_sha256)
    if (
        candidate["schema_version"] != "candidate/v1"
        or candidate["base_revision"] != bundle.base_revision
    ):
        raise ValueError("Candidate does not match the evidence base revision")
    if len(candidate["files"]) > MAX_FILES:
        raise ValueError("Evidence candidate contains too many files")
    total = 0
    for value in candidate["files"].values():
        if value["mode"] not in (0o644, 0o755):
            raise ValueError("Evidence candidate has an unsupported file mode")
        total += len(base64.b64decode(value["data"], validate=True))
    if total > MAX_BYTES:
        raise ValueError("Evidence candidate exceeds the size limit")
    if set(candidate["changed_paths"]) - set(bundle.allowed_paths):
        raise ValueError("Evidence candidate exceeds the allowed scope")
    expected_artifacts = {bundle.candidate_sha256, candidate["patch_sha256"]}
    if bundle.trace_sha256:
        trace = json.loads(artifacts.get(bundle.trace_sha256))
        if (
            trace.get("schema_version") != "development-trace/v1"
            or trace.get("workflow_id") != bundle.run.workflow_id
        ):
            raise ValueError("Trace belongs to another workflow")
        expected_artifacts.add(bundle.trace_sha256)
    checked = False
    if report := bundle.verification:
        if report.candidate != bundle.candidate_sha256:
            raise ValueError("Verification belongs to another candidate")
        if report.recipe_sha256 != digest(canonical(bundle.recipe.model_dump())):
            raise ValueError("Verification belongs to another recipe")
        if report.passed != (report.exit_code == 0 and report.reason is None):
            raise ValueError("Verification outcome contradicts the process result")
        artifacts.get(report.output_sha256)
        expected_artifacts.add(report.output_sha256)
        checked = report.passed
    reviewed = False
    if bundle.review is not None:
        verdict = validate(canonical(bundle.review), artifacts.get(candidate["patch_sha256"]))
        reviewed = not verdict.blocked
    if set(bundle.artifacts) != expected_artifacts:
        raise ValueError("Bundle contains unrelated or missing artifacts")
    return {
        "schema_version": bundle.schema_version,
        "bundle_sha256": sha,
        "candidate_sha256": bundle.candidate_sha256,
        "base_revision": bundle.base_revision,
        "tree_sha256": candidate["tree_sha256"],
        "patch_sha256": candidate["patch_sha256"],
        "artifact_count": len(expected_artifacts),
        "changed_paths": candidate["changed_paths"],
        "verification_passed": checked,
        "review_clear": reviewed,
        "state": "verified" if checked and reviewed else "evidence_incomplete",
    }


def write_bundle(directory, destination):
    raw = export_workflow(directory)
    atomic_write(destination, raw)
    return {**verify_bundle(raw), "path": str(destination.resolve())}
