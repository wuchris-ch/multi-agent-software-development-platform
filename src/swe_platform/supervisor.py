import base64
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import fixture
from .io import Artifacts, atomic_write, canonical, locked
from .models import TERMINAL, Completion, State
from .sandbox.docker import Docker, SandboxError


class Supervisor:
    def __init__(self, store, owner, fault=lambda _: None):
        self.store, self.owner, self.fault = store, owner, fault
        self.artifacts = Artifacts(store.root / "artifacts")
        self.children = {}
        self.docker = Docker(store.root / "containers")

    def dispatch(self, job, attempt):
        path = Path(attempt["workspace"])
        fixture.prepare(path / "repo")
        spec = json.loads(job["payload"])
        spec.update(attempt_id=attempt["id"], epoch=attempt["epoch"], deadline=job["deadline"])
        atomic_write(path / "spec.json", canonical(spec))
        if spec["adapter"] == "docker-scripted":
            code = fixture.BASE if spec["behavior"] == "wrong" else fixture.FIXED
            script = (
                "import time; from pathlib import Path; "
                f"time.sleep({spec['delay_seconds']!r}); "
                f"Path('calculator.py').write_bytes({code!r})"
            )
            self.docker.start(
                attempt["id"],
                {},
                ["python", "-c", script],
                ["calculator.py"],
                timeout=spec["deadline_seconds"],
            )
            self.fault("after_spawn")
            return
        # Only installed platform code executes here. No task text becomes code/argv.
        child = subprocess.Popen(
            [sys.executable, "-m", "swe_platform.worker", str(path)],
            cwd=path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(path)},
        )
        self.children[attempt["id"]] = child
        self.fault("after_spawn")

    def stop(self, attempt):
        if not attempt:
            return True
        job = self.store.get(attempt["job_id"])
        if json.loads(job["payload"])["adapter"] == "docker-scripted":
            return self.docker.cancel(attempt["id"])
        path = Path(attempt["workspace"])
        tombstone = path / "cancel"
        if not tombstone.exists():
            atomic_write(tombstone, b"cancel requested\n")
        if not locked(path / "execution.lock"):
            return True
        if time.time() - tombstone.stat().st_mtime > 0.3:
            receipt = path / "started.json"
            if receipt.exists():
                pid = json.loads(receipt.read_text())["pid"]
                # Never kill a reused PID based only on a historical numeric handle.
                ps = subprocess.run(
                    # Linux ps truncates long commands by default, including the execution
                    # path used to guard against PID reuse. Request the complete argv.
                    ["ps", "-ww", "-p", str(pid), "-o", "command="],
                    capture_output=True,
                    text=True,
                    timeout=2,
                ).stdout
                if "swe_platform.worker" in ps and str(path) in ps:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        return not locked(path / "execution.lock")

    def reconcile(self):
        for key, child in list(self.children.items()):
            if child.poll() is not None:
                del self.children[key]
        jobs = self.store.jobs()
        active = [j for j in jobs if j["state"] not in TERMINAL and j["state"] != State.queued]
        if not active:
            queued = next((j for j in jobs if j["state"] == State.queued), None)
            if queued:
                if queued["deadline"] <= time.time():
                    self.store.transition(
                        queued["id"],
                        queued["version"],
                        State.needs_attention,
                        error="Job deadline expired before dispatch",
                    )
                else:
                    self.store.launch_intent(queued["id"], self.owner)
                    self.fault("after_intent")
        for job in self.store.jobs():
            if job["state"] in TERMINAL or job["state"] == State.queued:
                continue
            try:
                self.reconcile_job(job)
            except (SandboxError, OSError, ValueError, KeyError) as exc:
                current = self.store.get(job["id"])
                # Preserve cancellation state if Docker cannot confirm termination.
                with self.store.transaction():
                    self.store.db.execute(
                        "UPDATE jobs SET error=? WHERE id=?", (str(exc), job["id"])
                    )
                if current["state"] != State.cancelling:
                    try:
                        stopped = self.stop(self.store.attempt(job["id"]))
                    except (SandboxError, OSError):
                        stopped = False
                    if stopped:
                        self.store.transition(
                            job["id"], current["version"], State.needs_attention, error=str(exc)
                        )

    def reconcile_job(self, job):
        self.store.adopt(job["id"], self.owner)
        job = self.store.get(job["id"])
        attempt = self.store.attempt(job["id"])
        if job["state"] == State.cancelling:
            if self.stop(attempt):
                self.store.transition(
                    job["id"],
                    job["version"],
                    State.cancelled,
                    owner=self.owner,
                    epoch=job["epoch"],
                )
            return
        if job["deadline"] <= time.time():
            if self.stop(attempt):
                self.store.transition(
                    job["id"],
                    job["version"],
                    State.needs_attention,
                    error="Deadline expired; execution stopped",
                )
            return
        path = Path(attempt["workspace"])
        if job["state"] == State.implementing:
            if json.loads(job["payload"])["adapter"] == "docker-scripted":
                state = self.docker.inspect(attempt["id"])
                if state is None or state["State"]["Status"] == "created":
                    self.dispatch(job, attempt)
                    return
                if state["State"]["Running"]:
                    return
                output = self.docker.collect(attempt["id"])
                outcome = (
                    "completed" if output["exit_code"] == 0 and not output["reason"] else "error"
                )
                if outcome == "completed":
                    code = base64.b64decode(output["files"]["calculator.py"]["data"], validate=True)
                    if code not in (fixture.BASE, fixture.FIXED):
                        raise ValueError("Unexpected scripted source")
                    atomic_write(path / "repo/calculator.py", code)
                result = Completion(
                    attempt_id=attempt["id"], epoch=attempt["epoch"], outcome=outcome
                )
                self.fault("before_completion")
                self.store.complete(job["id"], self.owner, result)
                return
            if locked(path / "execution.lock"):
                return
            if (path / "result.json").exists():
                result = Completion.model_validate_json((path / "result.json").read_bytes())
                self.fault("before_completion")
                self.store.complete(job["id"], self.owner, result)
            elif (path / "started.json").exists():
                self.store.transition(
                    job["id"],
                    job["version"],
                    State.needs_attention,
                    error="Worker stopped without a completion receipt",
                )
            elif attempt["id"] not in self.children:
                self.dispatch(job, attempt)
        elif job["state"] == State.verifying:
            candidate, report_sha, report = fixture.verify(path / "repo", self.artifacts)
            self.fault("after_artifacts")
            target = State.ready_local if report["passed"] else State.rejected
            self.store.transition(
                job["id"],
                job["version"],
                target,
                owner=self.owner,
                epoch=job["epoch"],
                candidate=candidate,
                report=report_sha,
            )

    def inspect(self, job_id):
        result = self.store.inspect(job_id)
        if result["report"]:
            report = json.loads(self.artifacts.get(result["report"]))
            self.artifacts.get(result["candidate"])
            result["evidence"] = report
            result["patch"] = self.artifacts.get(report["patch"]).decode()
            result["artifact_directory"] = str(self.artifacts.root)
        return result
