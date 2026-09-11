import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import eventually

from swe_platform.io import locked
from swe_platform.models import Submission


def submit(service, **kwargs):
    return service.call("submit", submission=Submission(key="fixture", **kwargs).model_dump())["id"]


def test_fixture_ready_local_and_duplicate(service):
    job_id = submit(service)
    assert submit(service) == job_id
    with pytest.raises(ValueError, match="different payload"):
        submit(service, task="another task")
    service.wait_state(job_id, "ready_local")
    info = service.call("inspect", job_id=job_id)
    assert info["evidence"]["passed"]
    assert info["evidence"]["candidate"] == info["candidate"]
    assert "+    return a + b" in info["patch"]
    assert len(info["attempts"]) == len(info["effects"]) == 1
    assert info["effects"][0]["state"] == "confirmed"


@pytest.mark.parametrize(
    "fault", ["after_intent", "after_spawn", "before_completion", "after_artifacts"]
)
def test_process_crash_boundaries(service, fault):
    service.stop()
    service.start(fault)
    job_id = submit(service, delay_seconds=0.5)
    eventually(lambda: service.process.poll() == 86)
    service.stop()
    service.start()
    service.wait_state(job_id, "ready_local")
    info = service.call("inspect", job_id=job_id)
    assert len(info["attempts"]) == 1
    assert len(info["effects"]) == 1
    assert info["effects"][0]["state"] == "confirmed"
    assert info["evidence"]["passed"]


def test_sigkill_coordinator_adopts_same_live_worker(service):
    job_id = submit(service, delay_seconds=1.5)
    info = eventually(
        lambda: j if (j := service.call("inspect", job_id=job_id))["attempts"] else None
    )
    path = Path(info["attempts"][0]["workspace"])
    eventually(lambda: (path / "started.json").exists())
    receipt = (path / "started.json").read_bytes()
    service.stop(kill=True)
    assert locked(path / "execution.lock")
    service.start()
    service.wait_state(job_id, "ready_local")
    assert (path / "started.json").read_bytes() == receipt


def test_duplicate_dispatch_cannot_write_twice(service):
    job_id = submit(service, delay_seconds=0.7)
    info = eventually(
        lambda: j if (j := service.call("inspect", job_id=job_id))["attempts"] else None
    )
    path = Path(info["attempts"][0]["workspace"])
    eventually(lambda: (path / "started.json").exists())
    receipt = (path / "started.json").read_bytes()
    process = subprocess.run([sys.executable, "-m", "swe_platform.worker", str(path)], timeout=3)
    assert process.returncode == 0
    service.wait_state(job_id, "ready_local")
    assert receipt == (path / "started.json").read_bytes()


def test_second_service_cannot_own_state(service):
    other = subprocess.run(
        [sys.executable, "-m", "swe_platform", "--state", str(service.root), "serve"],
        capture_output=True,
        timeout=3,
    )
    assert other.returncode != 0
    assert b"Another service" in other.stderr
    assert service.ping()


@pytest.mark.parametrize("behavior", ["fix", "ignore_cancel"])
def test_cancel_confirms_worker_stopped(service, behavior):
    job_id = submit(service, delay_seconds=10, behavior=behavior)
    info = eventually(
        lambda: j if (j := service.call("inspect", job_id=job_id))["attempts"] else None
    )
    path = Path(info["attempts"][0]["workspace"])
    eventually(lambda: (path / "started.json").exists())
    assert service.call("cancel", job_id=job_id)["state"] == "cancelling"
    started = time.monotonic()
    service.wait_state(job_id, "cancelled")
    assert time.monotonic() - started < 3
    assert not locked(path / "execution.lock")
    assert (path / "cancel").exists()
    assert "return a - b" in (path / "repo/calculator.py").read_text()


def test_wrong_patch_is_rejected(service):
    job_id = submit(service, behavior="wrong")
    service.wait_state(job_id, "rejected")
    assert service.call("inspect", job_id=job_id)["evidence"]["exit_code"] != 0


def test_worker_without_receipt_is_not_relaunched(service):
    job_id = submit(service, delay_seconds=10)
    info = eventually(
        lambda: j if (j := service.call("inspect", job_id=job_id))["attempts"] else None
    )
    path = Path(info["attempts"][0]["workspace"])
    eventually(lambda: (path / "started.json").exists())
    import os
    import signal

    os.kill(json.loads((path / "started.json").read_text())["pid"], signal.SIGKILL)
    service.wait_state(job_id, "needs_attention")
    assert len(service.call("inspect", job_id=job_id)["attempts"]) == 1


def test_docker_job_recovers_without_duplicate_container(service):
    from swe_platform.sandbox.docker import Docker

    if subprocess.run(["docker", "info"], capture_output=True).returncode:
        pytest.skip("Docker unavailable")
    service.stop()
    service.start("after_spawn")
    job_id = submit(service, adapter="docker-scripted", delay_seconds=1.5)
    eventually(lambda: service.process.poll() == 86)
    service.stop()
    service.start()
    service.wait_state(job_id, "ready_local")
    info = service.call("inspect", job_id=job_id)
    assert len(info["attempts"]) == 1
    docker = Docker(service.root / "containers")
    attempt_id = info["attempts"][0]["id"]
    assert docker.inspect(attempt_id)["State"]["ExitCode"] == 0
    docker.remove(attempt_id)
