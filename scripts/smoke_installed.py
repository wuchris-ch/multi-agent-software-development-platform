"""Run with a clean wheel environment, from a directory outside this checkout."""

import json
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from importlib.resources import files
from pathlib import Path

import swe_platform
from swe_platform.models import Submission
from swe_platform.sandbox.docker import Docker
from swe_platform.service import request


def wait_for(function, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = function()
            if result:
                return result
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    raise AssertionError("Installed-package smoke timed out")


def main():
    assert "site-packages" in Path(swe_platform.__file__).parts
    for resource in (
        "migrations/001.sql",
        "sandbox/runner.py",
        "broker/worker.py",
        "broker/gateway.mjs",
        "static/index.html",
    ):
        assert files("swe_platform").joinpath(resource).read_bytes()
    with tempfile.TemporaryDirectory(prefix="swe-wheel-", dir="/tmp") as tmp:
        root = Path(tmp).resolve()
        process = subprocess.Popen(
            [sys.executable, "-m", "swe_platform", "--state", str(root), "serve"],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        job = None
        try:
            wait_for(lambda: request(root, "status") == [])
            job = request(
                root,
                "submit",
                submission=Submission(
                    key="installed-wheel", adapter="docker-scripted"
                ).model_dump(),
            )
            wait_for(lambda: request(root, "status", job_id=job["id"])["state"] == "ready_local")
            result = request(root, "inspect", job_id=job["id"])
            assert result["evidence"]["passed"]
            assert len(result["attempts"]) == 1
            assert "+    return a + b" in result["patch"]
            print(
                json.dumps(
                    {
                        "installed_version": swe_platform.__version__,
                        "state": result["state"],
                        "packaged_resources": "verified",
                        "docker_fixture": "passed",
                    }
                )
            )
        finally:
            if job and process.poll() is None:
                request(root, "cancel", job_id=job["id"])
                wait_for(
                    lambda: (
                        request(root, "status", job_id=job["id"])["state"]
                        in {"ready_local", "cancelled", "rejected", "needs_attention"}
                    )
                )
                for attempt in request(root, "inspect", job_id=job["id"])["attempts"]:
                    Docker(root / "containers").remove(attempt["id"])
            process.terminate()
            process.wait(timeout=5)
            diagnostic = process.stderr.read()
            process.stderr.close()
            assert process.returncode == 0, diagnostic.decode(errors="replace")

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        token = secrets.token_urlsafe(32)
        script = root / "console-smoke.py"
        script.write_text(
            "from pathlib import Path\nimport uvicorn\n"
            "from swe_platform.console import create_app\n"
            f"uvicorn.run(create_app(Path({str(root)!r}), {token!r}, "
            f"origin='http://127.0.0.1:{port}'), host='127.0.0.1', port={port}, access_log=False)\n"
        )
        console = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:

            def check_console():
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                        return b'<div id="root">' in response.read()
                except (OSError, urllib.error.URLError):
                    return False

            wait_for(check_console)
            call = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/workflows",
                headers={"Authorization": "Bearer " + token},
            )
            with urllib.request.urlopen(call, timeout=3) as response:
                assert json.load(response)["items"] == []
            print(json.dumps({"installed_console": "passed", "packaged_react_assets": "verified"}))
        finally:
            console.terminate()
            console.wait(timeout=5)
            diagnostic = console.stderr.read()
            console.stderr.close()
            assert console.returncode in (0, -signal.SIGTERM), diagnostic.decode(errors="replace")
            assert b"Application shutdown complete" in diagnostic


if __name__ == "__main__":
    main()
