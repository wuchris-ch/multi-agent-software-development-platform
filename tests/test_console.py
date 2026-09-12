import json
import shutil
import time

import pytest
from fastapi.testclient import TestClient
from test_github_publication import publication as publication
from test_snapshot import repository as repository

from swe_platform.console import create_app
from swe_platform.io import atomic_write, canonical, digest

OPERATOR = "operator-session-" + "a" * 32
VIEWER = "viewer-session-" + "b" * 32
ORIGIN = "http://127.0.0.1:8765"


@pytest.fixture
def console(publication, tmp_path):
    publisher, remote, plan_sha = publication
    root = tmp_path / "console-state"
    key = "retry-task"
    identifier = digest(key.encode())
    directory = root / "workflows" / identifier
    shutil.copytree(publisher.root.parent, directory / "evidence")
    plan = publisher.load(plan_sha)
    record = {
        "key": key,
        "request": {
            "source": "/private/work/retry-helper",
            "task": "Fix retry handling",
            "max_requests": 30,
            "max_repairs": 2,
            "coding_profile": "PRIVATE-PROVIDER-CANARY",
        },
        "state": "ready_local",
        "base": {"base_revision": plan.base_sha},
        "deadline": time.time() + 100,
        "events": [{"seq": 1, "at": time.time(), "event": "workflow.created"}],
        "steps": {
            "coder-0": {
                "reserved_requests": 12,
                "result": {
                    "model_requests": 2,
                    "files": {"private-worker-file": "PRIVATE-PROVIDER-CANARY"},
                },
            }
        },
        "candidates": [plan.candidate_sha256],
        "active": None,
    }
    atomic_write(directory / "job.json", canonical(record))
    app = create_app(root, OPERATOR, viewer_token=VIEWER, github_factory=lambda: remote)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, remote, identifier, plan_sha, directory


def auth(token=OPERATOR):
    return {"Authorization": "Bearer " + token}


def test_console_requires_session_and_rejects_cross_origin_and_host(console):
    client, remote, identifier, plan_sha, _ = console
    assert client.get("/api/workflows").status_code == 401
    assert client.get("/api%2Fworkflows").status_code == 401
    assert (
        client.get("/api/workflows", headers={**auth(), "Host": "attacker.invalid"}).status_code
        == 403
    )
    assert (
        client.get(
            "/api/workflows", headers={**auth(), "Origin": "https://attacker.invalid"}
        ).status_code
        == 403
    )
    response = client.post(
        f"/api/workflows/{identifier}/publications/{plan_sha}/publish",
        json={"plan_sha256": plan_sha},
        headers=auth(VIEWER),
    )
    assert response.status_code == 403
    assert remote.writes == []


def test_console_read_model_omits_worker_payload_and_provider_configuration(console):
    client, _, identifier, _, _ = console
    listing = client.get("/api/workflows", headers=auth(VIEWER))
    assert listing.status_code == 200 and listing.json()["total"] == 1
    response = client.get(f"/api/workflows/{identifier}", headers=auth(VIEWER))
    assert response.status_code == 200
    data = response.json()
    assert data["requests_used_or_reserved"] == 2
    assert data["candidate"]["state"] == "ready_local"
    assert data["candidate"]["check_output"] == "fixture checks passed"
    assert "PRIVATE-PROVIDER-CANARY" not in response.text + listing.text
    assert "/private/work" not in response.text + listing.text
    assert data["stages"][0]["name"] == "coder-0"
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_console_diff_is_bound_to_workflow_candidate_and_event_cursor(console):
    client, _, identifier, _, directory = console
    record = json.loads((directory / "job.json").read_bytes())
    sha = record["candidates"][0]
    result = client.get(f"/api/workflows/{identifier}/diff?candidate={sha}", headers=auth()).json()
    assert result["candidate"] == sha and "+value = 2" in result["diff"]
    assert (
        client.get(
            f"/api/workflows/{identifier}/diff?candidate={'e' * 64}", headers=auth()
        ).status_code
        == 409
    )
    first = client.get(f"/api/workflows/{identifier}/events", headers=auth()).json()
    assert first["cursor"] == 1 and len(first["events"]) == 1
    second = client.get(f"/api/workflows/{identifier}/events?after=1", headers=auth()).json()
    assert second == {"events": [], "cursor": 1}


def test_operator_approves_exact_plan_then_read_only_reconciliation_avoids_duplicate(console):
    client, remote, identifier, plan_sha, _ = console
    path = f"/api/workflows/{identifier}/publications/{plan_sha}"
    response = client.post(path + "/publish", json={"plan_sha256": "e" * 64}, headers=auth())
    assert response.status_code == 409 and remote.writes == []
    response = client.post(path + "/publish", json={"plan_sha256": plan_sha}, headers=auth())
    assert response.status_code == 200 and response.json()["state"] == "published"
    writes = remote.writes.copy()
    assert client.post(path + "/reconcile", json={}, headers=auth()).json()["state"] == "published"
    assert remote.writes == writes


def test_console_rejects_stale_candidate_and_hides_invalid_input_values(console):
    client, remote, identifier, _, _ = console
    response = client.post(
        f"/api/workflows/{identifier}/publications",
        headers=auth(),
        json={
            "candidate": "f" * 64,
            "repository": "owner/repo",
            "branch": "development/other",
            "title": "Fix",
        },
    )
    assert response.status_code == 409 and remote.writes == []
    invalid = client.post(
        f"/api/workflows/{identifier}/publications",
        headers=auth(),
        json={"candidate": "PRIVATE-INPUT-CANARY", "repository": "INVALID-SECRET-CANARY"},
    )
    assert invalid.status_code == 422 and "CANARY" not in invalid.text
    assert client.get("/api/workflows/not-a-digest", headers=auth()).status_code == 404


def test_inactive_cancel_is_confirmed_and_persisted_without_a_live_coordinator(console):
    client, _, identifier, _, directory = console
    path = directory / "job.json"
    record = json.loads(path.read_bytes())
    record["state"] = "needs_attention"
    atomic_write(path, canonical(record))
    response = client.post(f"/api/workflows/{identifier}/cancel", json={}, headers=auth())
    assert response.status_code == 200 and response.json()["state"] == "cancelled"
    assert json.loads(path.read_bytes())["state"] == "cancelled"
    assert (directory / "cancel").exists()


def test_unreadable_run_does_not_hide_healthy_runs(console):
    client, _, _, _, directory = console
    other = directory.parent / ("f" * 64)
    atomic_write(other / "job.json", b"incomplete state")
    result = client.get("/api/workflows", headers=auth()).json()
    assert result["unavailable"] == 1 and len(result["items"]) == 1
