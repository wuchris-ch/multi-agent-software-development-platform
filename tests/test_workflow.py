import json
import re
import shlex
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_broker import FakeGateway
from test_snapshot import repository as repository

from swe_platform.candidates import Recipe, Workbench
from swe_platform.io import canonical
from swe_platform.sandbox.docker import PYTHON_IMAGE
from swe_platform.workflow import Workflow


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
    monkeypatch.setattr(Workbench, "register", original)
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
