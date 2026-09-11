import json
import os
import signal
import socket
import uuid
from pathlib import Path

from .io import canonical, lock
from .models import Submission
from .store import Store
from .supervisor import Supervisor

MAX_REQUEST = 32768


def socket_path(root: Path):
    path = str(root.resolve() / "service.sock")
    if len(path.encode()) >= 104:
        raise ValueError("State path too long for macOS Unix socket; use a shorter --state path")
    return path


def request(root: Path, operation, **data):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(10)
        sock.connect(socket_path(root))
        sock.sendall(canonical({"op": operation, **data}) + b"\n")
        buffer = bytearray()
        while chunk := sock.recv(65536):
            buffer.extend(chunk)
            if len(buffer) > 8 * 1024 * 1024:
                raise ValueError("Service response too large")
        result = json.loads(buffer)
        if "error" in result:
            raise ValueError(result["error"])
        return result["ok"]


def serve(root: Path, fault_name: str | None = None):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    with lock(root / "service.lock"):
        store = Store(root)
        owner = str(uuid.uuid4())

        def fault(name):
            if name == fault_name:
                os._exit(86)

        supervisor = Supervisor(store, owner, fault)
        path = Path(socket_path(root))
        path.unlink(missing_ok=True)
        running = True

        def shutdown(*_):
            nonlocal running
            running = False

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(path))
                path.chmod(0o600)
                server.listen(8)
                server.settimeout(0.05)
                print(f"Ready: {path}", flush=True)
                while running:
                    try:
                        connection, _ = server.accept()
                    except TimeoutError:
                        connection = None
                    if connection:
                        with connection:
                            connection.settimeout(0.5)
                            try:
                                data = bytearray()
                                while not data.endswith(b"\n"):
                                    chunk = connection.recv(4096)
                                    if not chunk:
                                        raise ValueError("Incomplete request")
                                    data.extend(chunk)
                                    if len(data) > MAX_REQUEST:
                                        raise ValueError("Request too large")
                                req = json.loads(data)
                                op = req["op"]
                                if op == "submit":
                                    result = store.submit(
                                        Submission.model_validate(req["submission"])
                                    )
                                elif op == "status":
                                    result = (
                                        store.get(req["job_id"])
                                        if req.get("job_id")
                                        else store.jobs()
                                    )
                                elif op == "inspect":
                                    result = supervisor.inspect(req["job_id"])
                                elif op == "cancel":
                                    result = store.cancel(req["job_id"])
                                else:
                                    raise ValueError("Unknown operation")
                                result = {"ok": result}
                            except (ValueError, KeyError, OSError, TypeError) as exc:
                                result = {"error": str(exc)}
                            try:
                                connection.sendall(canonical(result))
                            except (BrokenPipeError, TimeoutError):
                                pass
                    supervisor.reconcile()
        finally:
            path.unlink(missing_ok=True)
            store.close()
