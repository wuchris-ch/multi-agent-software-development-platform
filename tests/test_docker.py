import base64
import subprocess
import time
import uuid

import pytest

from swe_platform.sandbox.docker import Docker, SandboxError


@pytest.fixture
def sandbox(tmp_path):
    if subprocess.run(["docker", "info"], capture_output=True).returncode:
        pytest.skip("Docker is unavailable")
    docker = Docker(tmp_path)
    executions = []
    original = docker.start

    def start(execution_id, *args, **kwargs):
        executions.append(execution_id)
        return original(execution_id, *args, **kwargs)

    docker.start = start
    try:
        yield docker
    finally:
        for execution in set(executions):
            docker.cancel(execution)
            docker.remove(execution)


def test_isolated_task_and_idempotent_launch(sandbox):
    files = {
        "calculator.py": {"data": base64.b64encode(b"return_value = 1\n").decode(), "mode": 0o644}
    }
    command = [
        "python",
        "-c",
        "from pathlib import Path; Path('calculator.py').write_text('return_value = 2\\n')",
    ]
    result = sandbox.run("fixture", files, command, ["calculator.py"])
    assert result["exit_code"] == 0
    assert base64.b64decode(result["files"]["calculator.py"]["data"]) == b"return_value = 2\n"
    original_id = sandbox.inspect("fixture")["Id"]
    sandbox.start("fixture", files, command, ["calculator.py"])
    assert sandbox.inspect("fixture")["Id"] == original_id
    with pytest.raises(SandboxError, match="different request"):
        sandbox.start("fixture", files, ["false"])


def test_host_canaries_and_network_are_unreachable(sandbox, tmp_path, monkeypatch):
    canary = tmp_path / "host-secret"
    canary.write_text("HOST-CANARY")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ENV-CANARY")
    script = f"""
import os, socket
from pathlib import Path
assert not Path({str(canary)!r}).exists()
assert 'AWS_SECRET_ACCESS_KEY' not in os.environ
for path in ['/var/run/docker.sock', '/Users/chris', '/root/.codex', '/root/.kube', '/evaluator', '/service.sock']:
    try: assert not Path(path).exists(), path
    except PermissionError: pass
for host in ['1.1.1.1', '169.254.169.254', '192.168.1.1', '172.17.0.1']:
    sock = socket.socket(); sock.settimeout(.2)
    try: sock.connect((host, 80))
    except OSError: pass
    else: raise AssertionError('network reachable: ' + host)
    finally: sock.close()
print('isolation probes passed')
"""
    result = sandbox.run("canaries", {}, ["python", "-c", script])
    assert result["exit_code"] == 0, result
    assert "ENV-CANARY" not in result["output"]
    state = sandbox.inspect("canaries")
    assert state["HostConfig"]["NetworkMode"] == "none"
    assert state["HostConfig"]["ReadonlyRootfs"]


def test_disk_output_timeout_and_symlink_limits(sandbox):
    result = sandbox.run("flood", {}, ["python", "-c", "while True: print('x'*8192)"])
    assert result["reason"] == "output_limit"
    assert len(result["output"]) <= 256 * 1024
    result = sandbox.run(
        "disk", {}, ["python", "-c", "open('big', 'wb').write(b'x' * (70*1024*1024))"]
    )
    assert result["exit_code"] != 0
    assert "No space left" in result["output"]
    result = sandbox.run("timeout", {}, ["python", "-c", "import time; time.sleep(10)"], timeout=1)
    assert result["reason"] == "timeout"
    with pytest.raises(SandboxError):
        sandbox.run(
            "symlink",
            {},
            ["python", "-c", "import os; os.symlink('/etc/passwd', 'escape')"],
            ["escape"],
        )


def test_cancel_stops_grandchild_and_daemon_failure_is_not_confirmation(sandbox):
    execution = str(uuid.uuid4())
    script = "import os,signal,time; os.fork(); signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"
    sandbox.start(execution, {}, ["python", "-c", script], timeout=90)
    time.sleep(0.3)
    assert sandbox.inspect(execution)["State"]["Running"]
    start = time.monotonic()
    assert sandbox.cancel(execution)
    assert not sandbox.inspect(execution)["State"]["Running"]
    assert time.monotonic() - start < 5
    offline = Docker(sandbox.root, executable="/nonexistent/docker")
    with pytest.raises(SandboxError, match="termination not confirmed"):
        offline.cancel(execution)
