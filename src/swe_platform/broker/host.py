import base64
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from ..adapters.flue import collect
from ..credentials import GatewayProfile
from ..io import atomic_write, canonical, digest, lock
from ..process import bounded_run
from ..sandbox.docker import PYTHON_IMAGE, Docker, safe_path

AGENT_VERSION = "flue/2.0.3"


class AttachedOutput:
    """Drain attach output immediately, including while Docker confirms startup."""

    def __init__(self, process):
        self.process = process
        self.queue = queue.Queue(maxsize=4096)
        self.pending_bytes = 0
        self.buffer_lock = threading.Lock()
        self.overflow = threading.Event()
        self.streams = 2
        self.buffered = []
        self.threads = []
        for pipe in (process.stdout, process.stderr):
            thread = threading.Thread(target=self.drain, args=(pipe,), daemon=True)
            thread.start()
            self.threads.append(thread)

    def drain(self, pipe):
        while True:
            chunk = os.read(pipe.fileno(), 65536)
            with self.buffer_lock:
                if self.pending_bytes + len(chunk) > 4 * 1024 * 1024:
                    self.overflow.set()
                elif not self.overflow.is_set():
                    try:
                        self.queue.put_nowait((pipe, chunk))
                        self.pending_bytes += len(chunk)
                    except queue.Full:
                        self.overflow.set()
                # Drain and discard after overflow so stdout cannot deadlock the worker.
            if not chunk:
                return

    def poll(self, timeout):
        if self.overflow.is_set():
            raise ValueError("Worker attach output limit exceeded")
        if self.buffered:
            return [self.buffered.pop(0)]
        try:
            pipe, chunk = self.queue.get(timeout=timeout)
        except queue.Empty:
            return []
        with self.buffer_lock:
            self.pending_bytes -= len(chunk)
        if not chunk:
            self.streams -= 1
        return [(pipe, chunk)]

    def confirm_start(self, timeout):
        deadline = time.monotonic() + timeout
        data, errors = bytearray(), bytearray()
        while self.streams and time.monotonic() < deadline:
            for pipe, chunk in self.poll(0.1):
                if pipe is self.process.stderr:
                    errors.extend(chunk)
                    if len(errors) > 16384:
                        raise ValueError("Container diagnostic limit exceeded")
                    continue
                data.extend(chunk)
                if b"\n" in data:
                    line, _, tail = data.partition(b"\n")
                    if line != b'{"type":"ready"}':
                        raise ValueError("Invalid worker startup handshake")
                    if tail:
                        self.buffered.append((pipe, bytes(tail)))
                    return errors
                if len(data) > 1024:
                    raise ValueError("Worker startup handshake too large")
        raise ValueError("Container launch could not be confirmed")

    def close(self):
        for thread in self.threads:
            thread.join(timeout=2)
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            pipe.close()


class BrokerPolicy:
    """Every frame is untrusted, including frames spoofed by arbitrary worker code."""

    def __init__(self, model, *, deadline, max_requests=12, max_output_tokens=4096, role="coder"):
        if role not in ("coder", "reviewer"):
            raise ValueError("Unknown agent role")
        self.role = role
        self.model = model
        self.deadline = deadline
        self.max_requests = max_requests
        self.max_output_tokens = max_output_tokens
        self.requests = 0
        self.request_ids = set()

    def authorize(self, frame):
        if time.time() >= self.deadline or self.requests >= self.max_requests:
            raise ValueError("Model request allowance exhausted")
        if (
            not isinstance(frame, dict)
            or set(frame) != {"type", "id", "path", "body"}
            or frame["type"] != "model_request"
            or frame["path"] != "/v1/chat/completions"
        ):
            raise ValueError("Unsupported model operation")
        if not isinstance(frame["id"], str) or not 1 <= len(frame["id"]) <= 100:
            raise ValueError("Invalid request identity")
        if frame["id"] in self.request_ids:
            raise ValueError("Duplicate model request identity")
        if not isinstance(frame["body"], str) or len(frame["body"]) > 1400000:
            raise ValueError("Invalid model request body")
        data = base64.b64decode(frame["body"], validate=True)
        if len(data) > 1024 * 1024:
            raise ValueError("Model request too large")
        body = json.loads(data)
        allowed = {
            "model",
            "messages",
            "tools",
            "tool_choice",
            "stream",
            "stream_options",
            "max_tokens",
            "temperature",
            "parallel_tool_calls",
        }
        if not isinstance(body, dict) or set(body) - allowed:
            raise ValueError("Unsupported model request fields")
        if not isinstance(body.get("messages", []), list):
            raise ValueError("Invalid model messages")
        if not isinstance(body.get("tools", []), list):
            raise ValueError("Invalid tool definitions")
        permitted = (
            {"read", "write", "edit", "bash", "grep", "glob"} if self.role == "coder" else set()
        )
        for tool in body.get("tools", []):
            if (
                not isinstance(tool, dict)
                or tool.get("type") != "function"
                or not isinstance(tool.get("function"), dict)
                or tool["function"].get("name") not in permitted
            ):
                raise ValueError("Tool is not permitted for this agent role")

        def check(value):
            if isinstance(value, dict):
                if set(value) & {"image_url", "file_url", "file_id", "audio_url"}:
                    raise ValueError("Remote media and file handles are disabled")
                for nested in value.values():
                    check(nested)
            elif isinstance(value, list):
                for nested in value:
                    check(nested)

        check(body)
        requested = body.get("max_tokens", self.max_output_tokens)
        if type(requested) is not int or requested <= 0:
            raise ValueError("Invalid output token allowance")
        body["max_tokens"] = min(requested, self.max_output_tokens)
        body["model"] = self.model
        self.requests += 1
        self.request_ids.add(frame["id"])
        return body


def mask_model_metadata(raw, content_type):
    def replace(value):
        if isinstance(value, dict):
            if "model" in value:
                value["model"] = "worker"
            if isinstance(value.get("response"), dict) and "model" in value["response"]:
                value["response"]["model"] = "worker"
        return value

    if "text/event-stream" in content_type:
        lines = []
        for line in raw.decode().splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                line = "data: " + json.dumps(replace(json.loads(line[6:])), separators=(",", ":"))
            lines.append(line)
        return ("\n".join(lines) + "\n\n").encode()
    return canonical(replace(json.loads(raw)))


def validate_reply_tools(raw, content_type, role):
    """Reject unsolicited tools even if a runtime has additional built-ins registered."""
    permitted = {"read", "write", "edit", "bash", "grep", "glob"} if role == "coder" else set()
    names = {}
    if "text/event-stream" in content_type:
        values = [
            json.loads(line[6:])
            for line in raw.decode().splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
    else:
        values = [json.loads(raw)]
    for value in values:
        for choice in value.get("choices", []):
            message = choice.get("delta", choice.get("message", {}))
            if "function_call" in message:
                raise ValueError("Legacy model tool calls are disabled")
            for call in message.get("tool_calls", []):
                if call.get("type", "function") != "function":
                    raise ValueError("Unsupported model tool call")
                index = (choice.get("index", 0), call.get("index", call.get("id", "")))
                names[index] = names.get(index, "") + call.get("function", {}).get("name", "")
    if any(name not in permitted for name in names.values()):
        raise ValueError("Model returned a tool outside the agent role")


class HostGateway:
    def __init__(self, profile: GatewayProfile):
        self.environment = profile.environment()
        self.profile = profile
        self.identity_sha256 = digest(canonical(profile.model_dump()))
        self.node = shutil.which("node")
        if self.node is None:
            raise ValueError("Node is required for the model transport")

    def request(self, body, deadline):
        remaining = max(1, min(45, deadline - time.time()))
        payload = {
            "url": self.profile.base_url.rstrip("/") + "/chat/completions",
            "key": self.environment["MODEL_GATEWAY_API_KEY"],
            "body": body,
            "timeout_ms": int(remaining * 1000),
        }
        code, stdout, _ = bounded_run(
            [self.node, str(Path(__file__).with_name("gateway.mjs"))],
            payload=canonical(payload),
            env={"PATH": os.defpath},
            timeout=remaining + 2,
            limit=6 * 1024 * 1024,
        )
        if code:
            raise ValueError("Model transport process failed")
        response = json.loads(stdout)
        if response["status"] != 200:
            return (
                response["status"],
                "application/json",
                canonical({"error": {"message": "model gateway request failed"}}),
            )
        content_type = response["content_type"]
        raw = mask_model_metadata(base64.b64decode(response["body"]), content_type)
        return 200, content_type, raw


class AgentRun:
    def __init__(self, root: Path, image=None):
        self.root = root
        # The caller chooses a vetted immutable image, never repository configuration.
        image = image or os.environ.get("SWE_PLATFORM_AGENT_IMAGE")
        self.image_configured = bool(image)
        self.docker = Docker(root / "containers", image=image or PYTHON_IMAGE)

    def cancel(self, key):
        execution = digest(key.encode())
        directory = self.root / execution
        if not (directory / "intent.json").exists():
            raise ValueError("Unknown coding submission")
        # Cancellation can interrupt the run without acquiring its lifetime lock.
        atomic_write(directory / "cancel", b"cancel requested\n")
        self.docker.cancel(execution)
        return {
            "execution_id": execution,
            "state": "stopped",
            "receipt_available": (directory / "result.json").exists(),
        }

    def run(
        self,
        key,
        files,
        task,
        allowed,
        gateway,
        *,
        timeout=180,
        max_requests=12,
        role="coder",
        absolute_deadline=None,
    ):
        if role not in ("coder", "reviewer") or (role == "reviewer" and (files or allowed)):
            raise ValueError("Invalid agent role or reviewer capabilities")
        if not self.image_configured:
            raise ValueError(
                "Build the Flue image and set SWE_PLATFORM_AGENT_IMAGE or pass --image"
            )
        if (
            not key
            or len(key) > 200
            or not 1 <= len(task.encode()) <= (16000 if role == "coder" else 128 * 1024)
        ):
            raise ValueError("Invalid coding job identity or task")
        if not 1 <= timeout <= 1200 or not 1 <= max_requests <= 30:
            raise ValueError("Invalid coding budget")
        for name in [*files, *allowed]:
            safe_path(name)
        if sum(len(v["data"]) for v in files.values()) > 8 * 1024 * 1024:
            raise ValueError("Coding input too large")
        execution = digest(key.encode())
        directory = self.root / execution
        with lock(directory / "control.lock"):
            if (directory / "cancel").exists():
                raise ValueError("Coding attempt was cancelled; use a new submission key")
            spec = {
                "runtime": AGENT_VERSION,
                "role": role,
                "absolute_deadline": absolute_deadline,
                "files": files,
                "task": task,
                "allowed": allowed,
                "timeout": timeout,
                "max_requests": max_requests,
                "image": self.docker.image,
                "profile_sha256": gateway.identity_sha256,
            }
            request_sha = digest(canonical(spec))
            intent = directory / "intent.json"
            if intent.exists():
                previous = json.loads(intent.read_bytes())
                if previous["request_sha256"] != request_sha:
                    raise ValueError("Coding submission key already has another payload")
                receipt = directory / "result.json"
                if receipt.exists():
                    return json.loads(receipt.read_bytes())
                raise ValueError(
                    "Coding attempt has no receipt; reconcile its container before resubmitting"
                )
            deadline = min(time.time() + timeout, absolute_deadline or float("inf"))
            if time.time() >= deadline:
                raise ValueError("Agent deadline expired before dispatch")
            atomic_write(
                intent,
                canonical(
                    {"request_sha256": request_sha, "deadline": deadline, "execution_id": execution}
                ),
            )
            payload = directory / "input.json"
            atomic_write(payload, canonical({**spec, "deadline": deadline}))
            payload.chmod(0o444)
            runner = Path(__file__).with_name("worker.py").resolve()
            name = self.docker.name(execution)
            launch_control = self.docker.root / name
            with lock(launch_control / "control.lock"):
                if (launch_control / "cancel").exists() or (directory / "cancel").exists():
                    raise ValueError("Coding attempt was cancelled before launch")
                self.docker.command(
                    "create",
                    "--name",
                    name,
                    "--label",
                    f"swe.execution={execution}",
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
                    "512m",
                    "--memory-swap",
                    "512m",
                    "--pids-limit",
                    "96",
                    "--tmpfs",
                    "/work:rw,exec,nosuid,nodev,size=64m,mode=1777",
                    "--tmpfs",
                    "/tmp:rw,exec,nosuid,nodev,size=64m,mode=1777",
                    "--log-driver",
                    "none",
                    "--interactive",
                    "--mount",
                    f"type=bind,source={payload.resolve()},target=/input.json,readonly",
                    "--mount",
                    f"type=bind,source={runner},target=/runner.py,readonly",
                    "--entrypoint",
                    "python",
                    self.docker.image,
                    "-I",
                    "/runner.py",
                )
                process = subprocess.Popen(
                    [self.docker.executable, "start", "--attach", "--interactive", name],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                output = AttachedOutput(process)
                try:
                    # Output from the entrypoint proves launch without a concurrent
                    # status query, which can stall during attached starts.
                    # Keep launch ownership until this handshake or confirmed cancellation.
                    startup_errors = output.confirm_start(min(15, timeout))
                except BaseException:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    output.close()
                    self.docker._cancel(execution)
                    raise
            policy = BrokerPolicy(
                gateway.profile.model, deadline=deadline, max_requests=max_requests, role=role
            )
            frames = bytearray()
            errors = startup_errors
            completion = None
            try:
                while output.streams and time.time() < deadline + 3:
                    if (directory / "cancel").exists():
                        raise ValueError("Coding attempt was cancelled")
                    for pipe, chunk in output.poll(0.1):
                        if not chunk:
                            continue
                        if pipe is process.stderr:
                            errors.extend(chunk)
                            if len(errors) > 16384:
                                raise ValueError("Container diagnostic limit exceeded")
                            continue
                        frames.extend(chunk)
                        if len(frames) > 4 * 1024 * 1024:
                            raise ValueError("Worker frame limit exceeded")
                        while b"\n" in frames:
                            line, _, tail = frames.partition(b"\n")
                            frames = bytearray(tail)
                            frame = json.loads(line)
                            if frame.get("type") == "model_request":
                                if completion is not None:
                                    raise ValueError("Model request after completion")
                                body = policy.authorize(frame)
                                if (directory / "cancel").exists():
                                    raise ValueError("Coding attempt was cancelled")
                                status, content_type, data = gateway.request(body, deadline)
                                if status == 200:
                                    validate_reply_tools(data, content_type, role)
                                reply = {
                                    "id": frame["id"],
                                    "status": status,
                                    "content_type": content_type,
                                    "body": base64.b64encode(data).decode(),
                                }
                                process.stdin.write(canonical(reply) + b"\n")
                                process.stdin.flush()
                            elif frame.get("type") == "completion" and completion is None:
                                completion = frame
                            else:
                                raise ValueError("Invalid worker protocol frame")
                process.wait(timeout=3)
                state = self.docker.inspect(execution)
                if state is None or state["State"]["Running"]:
                    raise ValueError("Container termination is unconfirmed")
                if frames.strip():
                    raise ValueError("Truncated worker protocol frame")
                if not completion or process.returncode or completion["reason"]:
                    raise ValueError("Coding execution did not produce a complete candidate")
                parsed = collect(
                    base64.b64decode(completion["events"]), completion["exit_code"], role=role
                )
                if set(completion["files"]) != set(allowed):
                    raise ValueError("Candidate output scope mismatch")
                total = 0
                for value in completion["files"].values():
                    if value is None:
                        continue
                    if set(value) != {"data", "mode"} or value["mode"] not in (0o644, 0o755):
                        raise ValueError("Invalid candidate output file")
                    total += len(base64.b64decode(value["data"], validate=True))
                if total > 256 * 1024:
                    raise ValueError("Candidate output limit exceeded")
                result = {
                    "execution_id": execution,
                    "request_sha256": request_sha,
                    "files": completion["files"],
                    "model_requests": policy.requests,
                    "usage": parsed["usage"],
                    "cost_usd": None,
                    "adapter_version": AGENT_VERSION,
                    "role": role,
                    "message": parsed["message"],
                    "image": self.docker.image,
                    "profile_sha256": gateway.identity_sha256,
                    "status": "completed",
                }
                atomic_write(directory / "result.json", canonical(result))
                self.docker.remove(execution)
                return result
            except BaseException as exc:
                # Keep bounded worker diagnostics private, including nonzero CLI exits
                # discovered by collect(). Never include trusted profile values.
                frame = completion or {}
                try:
                    events = base64.b64decode(frame.get("events", ""), validate=True).decode(
                        errors="replace"
                    )
                except (ValueError, TypeError):
                    events = "Invalid worker event encoding"
                diagnostic = frame.get("diagnostic", errors.decode(errors="replace"))
                detail = {
                    "error_class": type(exc).__name__,
                    "attach_exit": process.poll(),
                    "worker_exit": frame.get("exit_code")
                    if type(frame.get("exit_code")) is int
                    else None,
                    "reason": frame.get("reason")
                    if frame.get("reason") in (None, "timeout", "output_limit", "scope_violation")
                    else "invalid_completion",
                    "model_requests": policy.requests,
                    "diagnostic": diagnostic[:8000]
                    if isinstance(diagnostic, str)
                    else "Invalid worker diagnostic",
                    "events": events[: 2 * 1024 * 1024],
                }
                protected = [gateway.profile.model, *getattr(gateway, "environment", {}).values()]
                endpoint = getattr(gateway.profile, "base_url", "")
                protected += [
                    endpoint,
                    urlsplit(endpoint).hostname or "",
                    getattr(gateway.profile, "keychain_service", ""),
                ]
                for field in ("diagnostic", "events"):
                    for value in sorted(set(filter(None, protected)), key=len, reverse=True):
                        detail[field] = detail[field].replace(value, "[redacted]")
                try:
                    atomic_write(directory / "failure.json", canonical(detail))
                finally:
                    # Even a diagnostic storage failure must stop the container.
                    self.docker.cancel(execution)
                raise
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
                output.close()
