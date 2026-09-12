"""Container-side model bridge. This file never receives an upstream credential."""

import base64
import hashlib
import http.server
import json
import os
import selectors
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

MAX_FRAME = 2 * 1024 * 1024
output_lock = threading.Lock()
pending = {}
pending_lock = threading.Lock()


def emit(value):
    with output_lock:
        print(json.dumps(value, separators=(",", ":")), flush=True)


def replies():
    buffer = bytearray()
    while True:
        # A daemon blocked on BufferedReader during interpreter shutdown can abort Python.
        chunk = os.read(0, 65536)
        if not chunk:
            return
        buffer.extend(chunk)
        if len(buffer) > 8 * 1024 * 1024:
            os._exit(3)
        while b"\n" in buffer:
            raw, _, tail = buffer.partition(b"\n")
            buffer = bytearray(tail)
            value = json.loads(raw)
            with pending_lock:
                waiter = pending.get(value.get("id"))
                if waiter:
                    waiter["response"] = value
                    waiter["event"].set()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        if self.path != "/v1/chat/completions" or not 0 < length <= 1024 * 1024:
            self.send_error(403)
            return
        body = self.rfile.read(length)
        request_id = str(uuid.uuid4())
        waiter = {"event": threading.Event()}
        with pending_lock:
            if len(pending) >= 2:
                self.send_error(429)
                return
            pending[request_id] = waiter
        try:
            emit(
                {
                    "type": "model_request",
                    "id": request_id,
                    "path": self.path,
                    "body": base64.b64encode(body).decode(),
                }
            )
            if not waiter["event"].wait(60):
                self.send_error(504)
                return
            result = waiter["response"]
            data = base64.b64decode(result["body"])
            self.send_response(result["status"])
            self.send_header("Content-Type", result["content_type"])
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        finally:
            with pending_lock:
                pending.pop(request_id, None)


def main():
    emit({"type": "ready"})
    spec = json.loads(Path("/input.json").read_bytes())
    root = Path("/work")
    os.chdir(root)
    for name, value in spec["files"].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(value["data"]))
        path.chmod(value["mode"])
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=replies, daemon=True).start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "OTEL_TRACES_EXPORTER": "none",
    }
    command = ["node", "/opt/agents/agents/run.mjs"]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    process.stdin.write(
        json.dumps(
            {
                "task": spec["task"]
                + (
                    "\nAllowed output paths: " + json.dumps(spec["allowed"])
                    if spec["role"] == "coder"
                    else ""
                ),
                "role": spec["role"],
                "deadline": spec["deadline"],
                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            }
        ).encode()
    )
    process.stdin.close()
    selector = selectors.DefaultSelector()
    buffers = {process.stdout: bytearray(), process.stderr: bytearray()}
    for pipe in buffers:
        selector.register(pipe, selectors.EVENT_READ)
    deadline = spec["deadline"]
    reason = None
    try:
        while selector.get_map():
            if time.time() > deadline:
                reason = "timeout"
                break
            for key, _ in selector.select(0.05):
                chunk = os.read(key.fd, 16384)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    buffers[key.fileobj].extend(chunk)
                    if sum(map(len, buffers.values())) > MAX_FRAME:
                        reason = "output_limit"
                        break
            if reason:
                break
        if not reason:
            process.wait(timeout=max(0.01, deadline - time.time()))
    except subprocess.TimeoutExpired:
        reason = "timeout"
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        selector.close()
    files = {}
    total = 0
    for name in spec["allowed"]:
        path = root / name
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError("symlink output")
        if not path.exists():
            files[name] = None
            continue
        if not path.is_file():
            raise ValueError("non-regular candidate")
        total += path.stat().st_size
        if total > 256 * 1024:
            raise ValueError("candidate output limit")
        data = path.read_bytes()
        files[name] = {
            "data": base64.b64encode(data).decode(),
            "mode": 0o755 if path.stat().st_mode & 0o111 else 0o644,
        }
    for name, original in spec["files"].items():
        if name in spec["allowed"]:
            continue
        path = root / name
        if (
            path.is_symlink()
            or any(p.is_symlink() for p in path.parents)
            or not path.is_file()
            or (0o755 if path.stat().st_mode & 0o111 else 0o644) != original["mode"]
            or hashlib.sha256(path.read_bytes()).digest()
            != hashlib.sha256(base64.b64decode(original["data"])).digest()
        ):
            reason = "scope_violation"
            break
    emit(
        {
            "type": "completion",
            "exit_code": process.returncode,
            "reason": reason,
            "events": base64.b64encode(buffers[process.stdout]).decode(),
            "diagnostic": buffers[process.stderr].decode(errors="replace")[:8000],
            "files": files,
        }
    )
    server.shutdown()
    server.server_close()


if __name__ == "__main__":
    main()
