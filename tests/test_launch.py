import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from swe_platform.console import create_app
from swe_platform.improvement import PolicyRegistry
from swe_platform.io import atomic_write, canonical, digest
from swe_platform.launch import ConsoleConfig, Launcher, LaunchRequest, Project
from swe_platform.policy import DevelopmentPolicy
from swe_platform.sandbox.docker import PYTHON_IMAGE


def config():
    return ConsoleConfig(
        projects=[
            Project(
                id="sample",
                title="Sample app",
                source=Path("/private/source-canary"),
                recipe={"image": PYTHON_IMAGE, "argv": ["python", "-m", "unittest"]},
                gateway_profile=Path("/private/profile-canary"),
                image="sha256:" + "a" * 64,
                allowed_paths=["app.py"],
            )
        ]
    )


def test_console_admission_is_bounded_idempotent_and_preserves_its_request(tmp_path):
    started, release = threading.Event(), threading.Event()
    calls = []

    def execute(project, request):
        calls.append(request.key)
        started.set()
        assert release.wait(5)
        directory = tmp_path / "workflows" / digest(request.key.encode())
        atomic_write(directory / "job.json", canonical({"state": "ready_local"}))
        return {"state": "ready_local"}

    launcher = Launcher(tmp_path, config(), execute=execute)
    request = LaunchRequest(key="one", project_id="sample", task="Update app")
    result = launcher.submit(request)
    assert started.wait(2)
    assert launcher.submit(request)["id"] == result["id"]
    with pytest.raises(ValueError, match="another request"):
        launcher.submit(request.model_copy(update={"task": "Different change"}))
    with pytest.raises(ValueError, match="already active"):
        launcher.submit(request.model_copy(update={"key": "two"}))
    assert not (tmp_path / "launches" / digest(b"two") / "request.json").exists()
    release.set()
    launcher.thread.join(3)
    assert launcher.inspect(result["id"])["state"] == "ready_local"
    assert launcher.submit(request)["state"] == "ready_local" and calls == ["one"]
    assert "canary" not in json.dumps(launcher.projects())
    launcher.close()


def test_active_policy_is_frozen_for_launch_and_resume(tmp_path):
    registry = PolicyRegistry(tmp_path)
    policy = DevelopmentPolicy.preset("selective")
    registry.initialize(policy)
    launcher = Launcher(tmp_path, config(), execute=lambda *_: {"state": "needs_attention"})
    request = LaunchRequest(key="policy", project_id="sample", task="Update app", mode="active")
    result = launcher.submit(request)
    launcher.thread.join(3)
    saved = json.loads((launcher.directory(result["id"]) / "request.json").read_bytes())
    assert saved["policy"] == policy.model_dump()
    launcher.resume(result["id"])
    launcher.thread.join(3)
    assert json.loads((launcher.directory(result["id"]) / "request.json").read_bytes()) == saved
    launcher.close()


def test_viewer_cannot_launch_and_client_cannot_supply_paths_or_budgets(tmp_path):
    launcher = Launcher(tmp_path, config(), execute=lambda *_: {"state": "needs_attention"})
    operator, viewer = "a" * 40, "b" * 40
    app = create_app(tmp_path, operator, viewer_token=viewer, launcher=launcher)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        data = {"key": "test", "project_id": "sample", "task": "Update app"}
        assert (
            client.post(
                "/api/launches", json=data, headers={"Authorization": "Bearer " + viewer}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/launches",
                json={**data, "source": "/private/elsewhere"},
                headers={"Authorization": "Bearer " + operator},
            ).status_code
            == 422
        )
        result = client.post(
            "/api/launches", json=data, headers={"Authorization": "Bearer " + operator}
        )
        assert result.status_code == 200
        launcher.thread.join(3)
        status = client.get(
            "/api/launches/" + result.json()["id"], headers={"Authorization": "Bearer " + viewer}
        ).json()
        assert status["state"] == "needs_attention" and status["workflow_id"] is None
        assert (
            "profile-canary"
            not in client.get("/api/projects", headers={"Authorization": "Bearer " + viewer}).text
        )
