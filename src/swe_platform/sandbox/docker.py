import base64
import json
import re
import subprocess
import time
from pathlib import Path, PurePosixPath

from ..io import atomic_write, canonical, digest, lock

PYTHON_IMAGE = "python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"


class SandboxError(RuntimeError):
    pass


def safe_path(name):
    path = PurePosixPath(name)
    if (
        not name
        or not path.parts
        or path.is_absolute()
        or str(path) != name
        or ".." in path.parts
        or ".git" in path.parts
        or "\\" in name
        or any(ord(c) < 32 for c in name)
    ):
        raise ValueError("Unsafe repository path")
    return name


class Docker:
    def __init__(self, root: Path, image=PYTHON_IMAGE, executable="docker"):
        if not re.fullmatch(r"[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}|sha256:[a-f0-9]{64}", image):
            raise ValueError("Sandbox image must be immutable")
        self.root, self.image, self.executable = root, image, executable

    def command(self, *args, timeout=15, check=True):
        try:
            result = subprocess.run([self.executable, *args], capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise SandboxError(
                "Docker unavailable or timed out; termination not confirmed"
            ) from None
        if check and result.returncode:
            raise SandboxError("Docker operation failed; inspect the execution before retrying")
        return result

    def name(self, execution_id):
        return "swe-" + digest((str(self.root.resolve()) + execution_id).encode())[:32]

    def inspect(self, execution_id):
        name = self.name(execution_id)
        result = self.command("container", "inspect", name, check=False)
        if result.returncode:
            # A failed inspect is not automatically proof of absence.
            self.command("info", "--format", "{{.ServerVersion}}")
            if b"No such" in result.stderr:
                return None
            raise SandboxError("Cannot determine container state")
        data = json.loads(result.stdout)[0]
        if data["Config"]["Labels"].get("swe.execution") != execution_id:
            raise SandboxError("Execution identity mismatch")
        return data

    def start(self, execution_id, files, argv, collect=(), timeout=30):
        directory = self.root / self.name(execution_id)
        with lock(directory / "control.lock"):
            if (directory / "cancel").exists():
                raise SandboxError("Execution was cancelled; use a new execution ID")
            return self._start(execution_id, files, argv, collect, timeout)

    def _start(self, execution_id, files, argv, collect=(), timeout=30):
        if not argv or any(not isinstance(a, str) or "\0" in a for a in argv):
            raise ValueError("Expected fixed argv")
        if not 0 < timeout <= 1200:
            raise ValueError("Invalid timeout")
        for name in [*files, *collect]:
            safe_path(name)
        if sum(len(f["data"]) for f in files.values()) > 8 * 1024 * 1024:
            raise ValueError("Sandbox input exceeds 8 MiB")
        for value in files.values():
            base64.b64decode(value["data"], validate=True)
            if value["mode"] not in (0o644, 0o755):
                raise ValueError("Unsupported mode")
        request = {"files": files, "argv": argv, "collect": list(collect), "timeout": timeout}
        request_sha = digest(canonical(request))
        current = self.inspect(execution_id)
        if current:
            if current["Config"]["Labels"].get("swe.request") != request_sha:
                raise SandboxError("Execution ID already has a different request")
            if current["State"]["Status"] == "created":
                self.command("start", self.name(execution_id))
            return self.name(execution_id)
        directory = self.root / self.name(execution_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = directory / "input.json"
        # Fixed on first creation: restarting a created execution cannot extend its deadline.
        if not payload.exists():
            atomic_write(payload, canonical({**request, "deadline": time.time() + timeout}))
            payload.chmod(0o444)
        elif (
            digest(
                canonical(
                    {k: v for k, v in json.loads(payload.read_bytes()).items() if k != "deadline"}
                )
            )
            != request_sha
        ):
            raise SandboxError("Stored execution intent differs")
        runner = Path(__file__).with_name("runner.py").resolve()
        self.command(
            "create",
            "--name",
            self.name(execution_id),
            "--label",
            f"swe.execution={execution_id}",
            "--label",
            f"swe.request={request_sha}",
            "--network",
            "none",
            "--read-only",
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--init",
            "--cpus",
            "1",
            "--memory",
            "256m",
            "--memory-swap",
            "256m",
            "--pids-limit",
            "64",
            "--tmpfs",
            "/work:rw,exec,nosuid,nodev,size=64m,mode=1777",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
            "--log-driver",
            "local",
            "--log-opt",
            "max-size=1m",
            "--log-opt",
            "max-file=1",
            "--log-opt",
            "compress=false",
            "--mount",
            f"type=bind,source={payload.resolve()},target=/input.json,readonly",
            "--mount",
            f"type=bind,source={runner},target=/runner.py,readonly",
            "--entrypoint",
            "python",
            self.image,
            "-I",
            "/runner.py",
        )
        self.command("start", self.name(execution_id))
        return self.name(execution_id)

    def cancel(self, execution_id):
        directory = self.root / self.name(execution_id)
        with lock(directory / "control.lock"):
            atomic_write(directory / "cancel", b"cancel requested\n")
            return self._cancel(execution_id)

    def _cancel(self, execution_id):
        current = self.inspect(execution_id)
        if current is None:
            return True
        if current["State"]["Running"]:
            self.command("kill", self.name(execution_id))
        state = self.inspect(execution_id)
        return state is not None and not state["State"]["Running"]

    def collect(self, execution_id):
        current = self.inspect(execution_id)
        if (
            current is None
            or current["State"]["Running"]
            or current["State"]["Status"] == "created"
        ):
            raise SandboxError("Execution has no terminal result")
        if current["State"]["ExitCode"] != 0:
            raise SandboxError("Sandbox entrypoint failed; no trustworthy completion envelope")
        result = self.command("logs", self.name(execution_id))
        if len(result.stdout) > 2 * 1024 * 1024:
            raise SandboxError("Sandbox result too large")
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise SandboxError("Malformed sandbox completion") from None

    def remove(self, execution_id):
        state = self.inspect(execution_id)
        if state and state["State"]["Running"]:
            raise SandboxError("Stop execution before removing it")
        if state:
            self.command("rm", self.name(execution_id))

    def run(self, execution_id, files, argv, collect=(), timeout=30):
        self.start(execution_id, files, argv, collect, timeout)
        deadline = time.monotonic() + timeout + 5
        while time.monotonic() < deadline:
            state = self.inspect(execution_id)
            if state and not state["State"]["Running"]:
                return self.collect(execution_id)
            time.sleep(0.1)
        self.cancel(execution_id)
        raise SandboxError("Execution exceeded supervisor deadline")
