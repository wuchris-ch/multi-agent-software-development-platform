import json
import re
import shlex
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_broker import FakeGateway
from test_snapshot import repository as repository

from swe_platform.candidates import Recipe, Workbench
from swe_platform.io import atomic_write, canonical, digest
from swe_platform.policy import DevelopmentPolicy
from swe_platform.sandbox.docker import PYTHON_IMAGE
from swe_platform.telemetry import execution_trace
from swe_platform.workflow import Workflow
from swe_platform.workspace.snapshot import git


class WorkflowGateway(FakeGateway):
    def __init__(self, *, block_first=False, command=None):
        super().__init__()
        self.starts = self.reviews = 0
        self.block_first, self.command = block_first, command

    def request(self, body, deadline):
        self.requests.append(body)
        if body.get("tools"):
            first = not any(message["role"] == "tool" for message in body["messages"])
            command = None
            if first:
                self.starts += 1
                command = self.command or (
                    "python -c "
                    + shlex.quote(
                        f"from pathlib import Path; Path('calc.py').write_text('value = {self.starts + 1}\\n')"
                    )
                )
            return FakeGateway(command).request(body, deadline)
        self.reviews += 1
        sha = re.search(r"input_sha256: ([a-f0-9]{64})", json.dumps(body["messages"]))[1]
        blocked = self.block_first and self.reviews == 1
        verdict = {
            "schema_version": "1.0",
            "input_sha256": sha,
            "risk": "medium" if blocked else "low",
            "blocked": blocked,
            "findings": [
                {
                    "severity": "major",
                    "category": "correctness",
                    "file": "calc.py",
                    "line": 1,
                    "detail": "The requested value is three.",
                }
            ]
            if blocked
            else [],
            "rationale": "Fixture review of the exact patch.",
        }
        return FakeGateway(message=json.dumps(verdict)).request(body, deadline)


def inputs(repo, tmp_path, gateway):
    workflow = Workflow(tmp_path / "state")
    recipe = Recipe(
        image=PYTHON_IMAGE, argv=["python", "-c", "import calc; assert calc.value >= 2"]
    )
    args = (
        "task",
        repo,
        "Set calc.value to three; run the public checks.",
        ["calc.py"],
        recipe,
        gateway,
    )
    return workflow, args


def test_real_flue_workflow_repairs_and_reuses_all_completed_stages(repository, tmp_path):  # noqa: F811
    gateway = WorkflowGateway(block_first=True)
    workflow, args = inputs(repository, tmp_path, gateway)
    result = workflow.run(*args)
    assert result["state"] == "ready_local"
    assert result["repairs_used"] == 1
    assert result["requests_used_or_reserved"] == len(gateway.requests) == 6
    assert gateway.starts == gateway.reviews == 2
    assert result["candidate"]["repairs_used"] == 1
    assert result["candidate"]["evidence"]["verification"]["passed"]
    first = result["steps"]["candidate-0"]["result"]["candidate"]
    bench = Workbench(workflow.directory("task") / "evidence")
    assert bench.inspect(first)["state"] == "stale"
    assert workflow.run(*args) == result
    assert len(gateway.requests) == 6
    assert (repository / "calc.py").read_text() == "value = 1\n"
    assert not any(
        "task" in [tool["function"]["name"] for tool in body.get("tools", [])]
        for body in gateway.requests
    )
    with pytest.raises(ValueError, match="different request"):
        workflow.run(*args, max_requests=31)


def test_workflow_recovers_after_coding_without_another_model_call(
    repository,
    tmp_path,
    monkeypatch,  # noqa: F811
):  # noqa: F811
    gateway = WorkflowGateway()
    workflow, args = inputs(repository, tmp_path, gateway)
    original = Workbench.register

    def crash(*_args, **_kwargs):
        raise RuntimeError("simulated interruption before candidate receipt")

    monkeypatch.setattr(Workbench, "register", crash)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        workflow.run(*args)
    assert len(gateway.requests) == 2

    def resumed(*call_args, **call_kwargs):
        record = json.loads((workflow.directory("task") / "job.json").read_bytes())
        assert record["state"] == "sealing"
        assert record["key"] == "task"
        return original(*call_args, **call_kwargs)

    monkeypatch.setattr(Workbench, "register", resumed)
    assert workflow.run(*args)["state"] == "ready_local"
    assert len(gateway.requests) == 3
    assert gateway.starts == 1


def test_shared_request_budget_cannot_reset_on_resume(repository, tmp_path):  # noqa: F811
    gateway = WorkflowGateway()
    workflow, args = inputs(repository, tmp_path, gateway)
    for _ in range(2):
        with pytest.raises(ValueError, match="budget exhausted"):
            workflow.run(*args, max_requests=2)
    result = workflow.inspect("task")
    assert result["state"] == "needs_attention"
    assert result["requests_used_or_reserved"] == len(gateway.requests) == 2
    assert not gateway.reviews


def test_single_mode_runs_public_checks_without_creating_review_evidence(repository, tmp_path):
    gateway = WorkflowGateway()
    workflow, args = inputs(repository, tmp_path, gateway)
    result = workflow.run(*args, policy=DevelopmentPolicy.preset("single"))
    assert result["state"] == "verified_local"
    assert gateway.starts == 1 and gateway.reviews == 0
    assert result["candidate"]["evidence"].get("review") is None
    assert result["accounting"]["model_requests"] == 2
    before = len(gateway.requests)
    assert workflow.run(*args, policy=DevelopmentPolicy.preset("single")) == result
    assert len(gateway.requests) == before


def test_selective_flue_plan_uses_two_read_only_specialists_and_one_writer(repository, tmp_path):
    (repository / "notes.txt").write_text("The consumer expects a numeric value.\n")
    subprocess.run(["git", "add", "notes.txt"], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "Add consumer context",
        ],
        cwd=repository,
        check=True,
    )
    workflow, _ = inputs(repository, tmp_path, None)
    barrier = threading.Barrier(2)

    class SelectiveGateway(WorkflowGateway):
        def request(self, body, deadline):
            messages = str(body["messages"])
            source = json.loads((workflow.directory("task") / "job.json").read_bytes())["base"][
                "files"
            ]
            if "You plan a bounded repository" in messages:
                self.requests.append(body)
                plan = {
                    "schema_version": "development-plan/v1",
                    "snapshot_sha256": digest(canonical(source)),
                    "summary": "Inspect the value and its consumer separately",
                    "steps": [
                        {"id": "change-value", "goal": "Update the value", "paths": ["calc.py"]}
                    ],
                    "specialists": [
                        {"id": "value", "focus": "Check value semantics", "paths": ["calc.py"]},
                        {
                            "id": "consumer",
                            "focus": "Check consumer expectations",
                            "paths": ["notes.txt"],
                        },
                    ],
                }
                return FakeGateway(message=json.dumps(plan)).request(body, deadline)
            if "You are a read-only repository specialist" in messages:
                self.requests.append(body)
                assert {tool["function"]["name"] for tool in body["tools"]} == {
                    "read",
                    "grep",
                    "glob",
                }
                selected = "consumer" if '"specialist_id":"consumer"' in messages else "value"
                path = "notes.txt" if selected == "consumer" else "calc.py"
                barrier.wait(timeout=20)
                handoff = {
                    "schema_version": "development-analysis/v1",
                    "snapshot_sha256": digest(canonical({path: source[path]})),
                    "specialist_id": selected,
                    "paths": [path],
                    "findings": ["The supplied source has a numeric consumer."],
                    "recommendation": "Preserve numeric behavior.",
                }
                return FakeGateway(message=json.dumps(handoff)).request(body, deadline)
            return super().request(body, deadline)

    gateway = SelectiveGateway()
    _, args = inputs(repository, tmp_path, gateway)
    result = workflow.run(*args, policy=DevelopmentPolicy.preset("selective"))
    assert result["state"] == "ready_local"
    assert gateway.starts == 1 and gateway.reviews == 1
    assert len(result["steps"]["analysis"]["result"]["handoffs"]) == 2
    assert result["accounting"]["model_requests"] == len(gateway.requests) == 6
    assert result["accounting"]["unresolved_calls"] == 0
    trace = execution_trace(workflow.directory("task"))
    assert len([span for span in trace["spans"] if span["kind"] == "model"]) == 6
    assert any(span["kind"] == "tool" for span in trace["spans"])
    assert "Preserve numeric behavior." not in json.dumps(trace)
    assert workflow.run(*args, policy=DevelopmentPolicy.preset("selective")) == result
    assert len(gateway.requests) == 6
    assert (repository / "calc.py").read_text() == "value = 1\n"


def test_analysis_failure_fences_all_siblings_even_if_one_stop_fails(
    repository, tmp_path, monkeypatch
):
    (repository / "notes.txt").write_text("Consumer context\n")
    git(repository, "add", "notes.txt")
    git(
        repository,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "Add analysis fixture",
    )
    workflow, args = inputs(repository, tmp_path, WorkflowGateway())
    barrier, released = threading.Barrier(2), threading.Event()
    stopped = []
    identities = ["specialist-code", "specialist-notes"]

    def run(agent, key, files, task, allowed, gateway, *, role="coder", **kwargs):
        if role == "planner":
            plan = {
                "schema_version": "development-plan/v1",
                "snapshot_sha256": digest(canonical(files)),
                "summary": "Inspect two separate source files",
                "steps": [{"id": "value", "goal": "Correct the value", "paths": ["calc.py"]}],
                "specialists": [
                    {"id": "code", "focus": "Inspect value", "paths": ["calc.py"]},
                    {"id": "notes", "focus": "Inspect consumer", "paths": ["notes.txt"]},
                ],
            }
            return {"message": json.dumps(plan), "model_requests": 1}
        assert role == "specialist", "Coding must not begin after failed analysis"
        atomic_write(agent.root / digest(key.encode()) / "intent.json", b"{}")
        barrier.wait(timeout=5)
        if key == identities[0]:
            raise ValueError("Analysis failed")
        released.wait(timeout=3)
        return {
            "message": json.dumps(
                {
                    "schema_version": "development-analysis/v1",
                    "snapshot_sha256": digest(canonical(files)),
                    "specialist_id": "notes",
                    "paths": ["notes.txt"],
                    "findings": [],
                    "recommendation": "Preserve behavior",
                }
            ),
            "model_requests": 1,
            "execution_id": digest(key.encode()),
        }

    def cancel(agent, key):
        assert all((agent.root / digest(item.encode()) / "cancel").exists() for item in identities)
        stopped.append(key)
        if key == identities[0]:
            raise OSError("Container stop unavailable")
        released.set()

    monkeypatch.setattr("swe_platform.workflow.AgentRun.run", run)
    monkeypatch.setattr("swe_platform.workflow.AgentRun.cancel", cancel)
    with pytest.raises(ValueError, match="group termination is unconfirmed"):
        workflow.run(*args, policy=DevelopmentPolicy.preset("selective"))
    assert stopped == identities
    assert released.is_set()
    assert workflow.inspect("task")["state"] == "needs_attention"


def test_workflow_cancel_stops_active_agent_and_prevents_next_stage(repository, tmp_path):  # noqa: F811
    gateway = WorkflowGateway(command="sleep 30")
    workflow, args = inputs(repository, tmp_path, gateway)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(workflow.run, *args)
        deadline = time.monotonic() + 15
        while not gateway.requests and not future.done() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert gateway.requests
        workflow.cancel("task")
        result = future.result(timeout=15)
    assert result["state"] == "cancelled"
    assert gateway.starts == 1 and gateway.reviews == 0
    assert workflow.run(*args)["state"] == "cancelled"


def test_cancellation_between_stage_intent_and_agent_admission_prevents_dispatch(
    repository, tmp_path, monkeypatch
):
    from threading import Event

    from swe_platform.broker.host import AgentRun

    gateway = WorkflowGateway()
    workflow, args = inputs(repository, tmp_path, gateway)
    admitted, release = Event(), Event()
    original = AgentRun.run

    def delayed(self, *call_args, **kwargs):
        admitted.set()
        assert release.wait(timeout=10)
        return original(self, *call_args, **kwargs)

    monkeypatch.setattr(AgentRun, "run", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(workflow.run, *args)
        assert admitted.wait(timeout=10)
        workflow.cancel("task")
        release.set()
        assert future.result(timeout=10)["state"] == "cancelled"
    assert not gateway.requests


def test_environment_credentials_are_explicit_and_not_serialized(monkeypatch):
    from swe_platform.credentials import GatewayProfile

    monkeypatch.setenv("FIXTURE_MODEL_KEY", "private-test-canary")
    profile = GatewayProfile(
        base_url="https://example.invalid/v1", model="model", api_key_env="FIXTURE_MODEL_KEY"
    )
    assert profile.environment()["MODEL_GATEWAY_API_KEY"] == "private-test-canary"
    assert b"private-test-canary" not in canonical(profile.model_dump())
    with pytest.raises(ValueError, match="exactly one"):
        GatewayProfile(
            base_url="https://example.invalid/v1",
            model="model",
            api_key_env="FIXTURE_MODEL_KEY",
            keychain_service="fixture",
        )


def test_standalone_flue_review_reuses_only_the_same_policy(repository, tmp_path):  # noqa: F811
    from test_workbench import PATCH

    gateway = WorkflowGateway()
    bench = Workbench(tmp_path / "state")
    recipe = Recipe(image=PYTHON_IMAGE, argv=["true"])
    sha = bench.intake(repository, PATCH, ["calc.py"], recipe)["candidate"]
    report = bench.review_agent(sha, gateway)
    assert not report["verdict"]["blocked"]
    assert report["model_requests"] == 1
    assert bench.review_agent(sha, gateway) == report
    assert len(gateway.requests) == 1
    with pytest.raises(ValueError, match="different runtime or policy"):
        bench.review_agent(sha, gateway, max_requests=3)
