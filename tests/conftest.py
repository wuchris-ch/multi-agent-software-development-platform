import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from swe_platform.sandbox.docker import Docker
from swe_platform.service import request


def eventually(function, timeout=8):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            last = function()
            if last:
                return last
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.03)
    raise AssertionError(f"Condition did not become true, last={last}")


class ServiceProcess:
    def __init__(self, root):
        self.root = root
        self.process = None

    def start(self, fault=None):
        argv = [sys.executable, "-m", "swe_platform", "--state", str(self.root), "serve"]
        if fault:
            argv += ["--fault", fault]
        self.process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        eventually(lambda: request(self.root, "status") == []) if not (
            self.root / "state.sqlite"
        ).exists() else eventually(lambda: self.ping())
        return self

    def ping(self):
        request(self.root, "status")
        return True

    def stop(self, kill=False):
        if self.process and self.process.poll() is None:
            self.process.kill() if kill else self.process.terminate()
            self.process.wait(timeout=5)
        if self.process:
            self.process.stdout.close()
            self.process.stderr.close()

    def call(self, op, **data):
        return request(self.root, op, **data)

    def wait_state(self, job_id, state):
        return eventually(
            lambda: j if (j := self.call("status", job_id=job_id))["state"] == state else None
        )


@pytest.fixture
def service():
    # macOS AF_UNIX path limit is 104 bytes; pytest's default path can exceed it.
    with tempfile.TemporaryDirectory(prefix="swe-test-", dir="/tmp") as tmp:
        svc = ServiceProcess(Path(tmp)).start()
        try:
            yield svc
        finally:
            if svc.process.poll() is not None:
                svc.start()
            for job in svc.call("status"):
                svc.call("cancel", job_id=job["id"])
            eventually(
                lambda: all(
                    j["state"] not in {"implementing", "cancelling", "queued", "verifying"}
                    for j in svc.call("status")
                )
            )
            docker = Docker(svc.root / "containers")
            for job in svc.call("status"):
                if '"docker-scripted"' in job["payload"]:
                    for attempt in svc.call("inspect", job_id=job["id"])["attempts"]:
                        docker.remove(attempt["id"])
            svc.stop()
