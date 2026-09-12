import base64
import json
import shlex
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from swe_platform.broker.host import AttachedOutput, BrokerPolicy, CodingRun, mask_model_metadata
from swe_platform.io import canonical


def frame(body, **changes):
    return {
        "type": "model_request",
        "id": "test-request",
        "path": "/v1/responses",
        "body": base64.b64encode(canonical(body)).decode(),
        **changes,
    }


def test_broker_enforces_route_model_and_quota():
    policy = BrokerPolicy("configured-model", deadline=time.time() + 5, max_requests=1)
    with pytest.raises(ValueError, match="Unsupported"):
        policy.authorize(frame({}, path="https://example.invalid/steal"))
    body = policy.authorize(
        frame({"model": "arbitrary", "store": True, "max_output_tokens": 99999})
    )
    assert body == {"model": "configured-model", "store": False, "max_output_tokens": 4096}
    with pytest.raises(ValueError, match="exhausted"):
        policy.authorize(frame({}))


@pytest.mark.parametrize(
    "body",
    [
        {"previous_response_id": "unrelated"},
        {"tools": [{"type": "web_search"}]},
        {"input": [{"type": "input_image", "image_url": "https://example.invalid"}]},
        {"input": [{"nested": {"file_id": "private-file"}}]},
        {"max_output_tokens": True},
        {"unknown": "field"},
    ],
)
def test_broker_rejects_remote_capabilities(body):
    with pytest.raises(ValueError):
        BrokerPolicy("configured-model", deadline=time.time() + 5).authorize(frame(body))


def test_response_metadata_uses_worker_alias():
    event = b'data: {"type":"response.created","response":{"model":"private-model"}}\n\n'
    masked = mask_model_metadata(event, "text/event-stream")
    assert b"private-model" not in masked
    assert b'"model":"worker"' in masked


class FakeGateway:
    profile = SimpleNamespace(model="fixture-model")
    identity_sha256 = "f" * 64

    def __init__(self, command=None):
        self.requests = []
        self.command = command

    def request(self, body, deadline):
        self.requests.append(body)
        item = {
            "id": "msg_fixture",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Done.", "annotations": []}],
        }
        if self.command and len(self.requests) == 1:
            item = {
                "id": "fc_fixture",
                "type": "function_call",
                "status": "completed",
                "call_id": "call_fixture",
                "name": "exec_command",
                "arguments": json.dumps(
                    {"cmd": self.command, "yield_time_ms": 1000, "max_output_tokens": 1000}
                ),
            }
        response = {
            "id": "resp_fixture",
            "object": "response",
            "status": "completed",
            "model": "worker",
            "output": [item],
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        }
        events = [
            {
                "type": "response.created",
                "response": {**response, "status": "in_progress", "output": []},
            },
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response},
        ]
        data = b"".join(b"data: " + canonical(event) + b"\n\n" for event in events)
        return 200, "text/event-stream", data


def test_real_cli_uses_broker_and_retains_receipt(tmp_path):
    gateway = FakeGateway()
    run = CodingRun(tmp_path)
    files = {"answer.txt": {"data": base64.b64encode(b"unchanged\n").decode(), "mode": 0o644}}
    args = (
        "fixture",
        files,
        "Say Done. Do not use tools or change files.",
        ["answer.txt"],
        gateway,
    )
    result = run.run(*args, timeout=45)
    assert result["status"] == "completed"
    assert result["files"] == files
    assert result["model_requests"] == 1
    assert run.run(*args, timeout=45) == result
    assert len(gateway.requests) == 1
    assert gateway.requests[0]["model"] == "fixture-model"
    assert run.docker.inspect(result["execution_id"]) is None
    with pytest.raises(ValueError, match="another payload"):
        run.run("fixture", files, "Different task", ["answer.txt"], gateway, timeout=45)
    stored = b"".join(path.read_bytes() for path in tmp_path.rglob("*.json"))
    assert b"fixture-model" not in stored
    assert json.loads(next(tmp_path.rglob("result.json")).read_bytes())["usage"]


def test_worker_tool_isolation_and_candidate_collection(tmp_path, monkeypatch):
    canary = tmp_path / "host-secret"
    canary.write_text("private-canary")
    monkeypatch.setenv("MODEL_GATEWAY_API_KEY", "private-canary")
    script = f"""
import os, socket
from pathlib import Path
assert 'MODEL_GATEWAY_API_KEY' not in os.environ
assert not Path({str(canary)!r}).exists()
assert not Path('/var/run/docker.sock').exists()
assert not Path('/tmp/codex/auth.json').exists()
assert os.getuid() == 65534
try:
    socket.create_connection(('8.8.8.8', 53), timeout=0.5)
except OSError:
    pass
else:
    raise AssertionError('external network available')
Path('answer.txt').write_text('isolated\\n')
print('ISOLATION_OK')
"""
    gateway = FakeGateway("python -c " + shlex.quote(script))
    run = CodingRun(tmp_path / "run")
    result = run.run(
        "isolation", {}, "Perform the requested tool call.", ["answer.txt"], gateway, timeout=45
    )
    assert result["model_requests"] == 2
    assert base64.b64decode(result["files"]["answer.txt"]["data"]) == b"isolated\n"
    assert "ISOLATION_OK" in json.dumps(gateway.requests[-1]["input"])
    assert "private-canary" not in json.dumps(gateway.requests)
    assert all(t["type"] in ("function", "custom") for t in gateway.requests[0]["tools"])


def test_exhausted_broker_stops_worker_and_never_replays_intent(tmp_path):
    gateway = FakeGateway("true")
    run = CodingRun(tmp_path)
    args = ("exhausted", {}, "Perform the requested tool call.", [], gateway)
    with pytest.raises(ValueError, match="exhausted"):
        run.run(*args, timeout=45, max_requests=1)
    assert len(gateway.requests) == 1
    execution = json.loads(next(tmp_path.rglob("intent.json")).read_bytes())["execution_id"]
    assert not run.docker.inspect(execution)["State"]["Running"]
    with pytest.raises(ValueError, match="no receipt"):
        run.run(*args, timeout=45, max_requests=1)
    assert len(gateway.requests) == 1
    run.docker.remove(execution)


def test_failed_cli_retains_redacted_diagnostic_without_success_receipt(tmp_path):
    class RejectedGateway(FakeGateway):
        def request(self, body, deadline):
            self.requests.append(body)
            return (
                400,
                "application/json",
                canonical({"error": {"message": "fixture-model rejected request"}}),
            )

    gateway = RejectedGateway()
    run = CodingRun(tmp_path)
    with pytest.raises(ValueError, match="Incomplete Codex"):
        run.run("rejected", {}, "Say done.", [], gateway, timeout=30)
    diagnostic = json.loads(next(tmp_path.rglob("failure.json")).read_bytes())
    assert diagnostic["worker_exit"] != 0
    assert diagnostic["model_requests"] == 1
    assert "rejected request" in diagnostic["events"]
    assert "fixture-model" not in json.dumps(diagnostic)
    assert not list(tmp_path.rglob("result.json"))
    execution = json.loads(next(tmp_path.rglob("intent.json")).read_bytes())["execution_id"]
    assert not run.docker.inspect(execution)["State"]["Running"]
    run.docker.remove(execution)


@pytest.mark.parametrize("size", [128 * 1024, 2 * 1024 * 1024, 10 * 1024 * 1024])
def test_attach_drains_before_startup_confirmation_and_bounds_backlog(size):
    process = subprocess.Popen(
        [sys.executable, "-c", f"import sys; sys.stdout.buffer.write(b'x' * {size})"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output = AttachedOutput(process)
    try:
        # Startup can wait for the child before the protocol consumer begins polling.
        process.wait(timeout=3)
        if size > 4 * 1024 * 1024:
            with pytest.raises(ValueError, match="output limit"):
                output.poll(0.1)
        else:
            received = bytearray()
            while output.streams:
                for pipe, data in output.poll(0.1):
                    if pipe is process.stdout:
                        received.extend(data)
            assert received == b"x" * size
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        output.close()


@pytest.mark.parametrize("valid", [True, False])
def test_startup_handshake_preserves_next_frame_and_rejects_early_exit(valid):
    data = b'{"type":"ready"}\nnext-frame\n' if valid else b""
    process = subprocess.Popen(
        [sys.executable, "-c", f"import sys; sys.stdout.buffer.write({data!r})"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output = AttachedOutput(process)
    try:
        if valid:
            assert output.confirm_start(3) == b""
            received = bytearray()
            while output.streams:
                for pipe, chunk in output.poll(0.1):
                    if pipe is process.stdout:
                        received.extend(chunk)
            assert received == b"next-frame\n"
        else:
            with pytest.raises(ValueError, match="launch could not be confirmed"):
                output.confirm_start(3)
    finally:
        process.wait(timeout=3)
        output.close()
