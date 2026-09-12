"""Durable orchestration of Flue agents and deterministic verification.

The coordinator decides transitions. Agents produce evidence, never authority.
Each job owns its snapshot, stage journal, request budget, and candidate lineage.
"""

import base64
import json
import shutil
import time
import uuid
from pathlib import Path

from .broker.host import AGENT_VERSION, AgentRun
from .candidates import Workbench
from .io import atomic_write, canonical, digest, lock
from .review.flue import validate
from .sandbox.docker import Docker, safe_path
from .workspace.snapshot import load_candidate, snapshot


class Workflow:
    def __init__(self, root: Path, image=None):
        self.root = root / "workflows"
        self.image = image

    def directory(self, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 200:
            raise ValueError("Invalid workflow key")
        return self.root / digest(key.encode())

    def inspect(self, key):
        directory = self.directory(key)
        record = json.loads((directory / "job.json").read_bytes())
        result = {
            "key": key,
            "state": record["state"],
            "steps": record["steps"],
            "events": record["events"],
            "deadline": record["deadline"],
            "request_budget": record["request"]["max_requests"],
            "requests_used_or_reserved": self.spent(record),
            "repairs_used": max(0, len(record["candidates"]) - 1),
            "cost_usd": None,
            "candidate": None,
        }
        if record["candidates"]:
            result["candidate"] = Workbench(directory / "evidence").inspect(
                record["candidates"][-1]
            )
            if result["state"] == "ready_local" and result["candidate"]["state"] != "ready_local":
                raise ValueError("Workflow evidence no longer establishes readiness")
        return result

    @staticmethod
    def spent(record):
        return sum(
            step.get("result", {}).get("model_requests", step["reserved_requests"])
            for step in record["steps"].values()
        )

    @staticmethod
    def save(directory, record, event):
        record["events"].append(
            {"seq": len(record["events"]) + 1, "at": time.time(), "event": event}
        )
        atomic_write(directory / "job.json", canonical(record))

    def cancel(self, key):
        directory = self.directory(key)
        with lock(directory / "dispatch.lock", timeout=15):
            record = json.loads((directory / "job.json").read_bytes())
            if record["state"] in ("ready_local", "cancelled"):
                return self.inspect(key)
            atomic_write(directory / "cancel", b"requested\n")
        active = record.get("active")
        if active and active["kind"] == "agent":
            agent = AgentRun(directory / "agents", image=record["request"]["image"])
            target = directory / "agents" / digest(active["key"].encode())
            # A tombstone also covers cancellation between job intent and agent launch.
            atomic_write(target / "cancel", b"requested\n")
            if (target / "intent.json").exists():
                agent.cancel(active["key"])
        elif active and active["kind"] == "verify":
            stopped = Docker(
                directory / "evidence/candidates/containers",
                image=record["request"]["recipe"]["image"],
            ).cancel(active["key"])
            if not stopped:
                raise ValueError("Verification termination is unconfirmed")
        try:
            with lock(directory / "control.lock"):
                record = json.loads((directory / "job.json").read_bytes())
                if record["state"] not in ("ready_local", "cancelled"):
                    record["state"], record["active"] = "cancelled", None
                    self.save(directory, record, "workflow.cancelled")
                return self.inspect(key)
        except BlockingIOError:
            pass  # The running coordinator confirms cancellation after its active stage exits.
        return {"key": key, "state": "cancellation_requested"}

    def run(
        self,
        key,
        source,
        task,
        allowed,
        recipe,
        gateway,
        *,
        review_gateway=None,
        timeout=600,
        max_requests=30,
        max_repairs=2,
    ):
        if not 1 <= timeout <= 3600 or not 1 <= max_requests <= 90 or not 0 <= max_repairs <= 2:
            raise ValueError("Invalid workflow budget")
        if not 1 <= len(task.encode()) <= 12000 or not allowed:
            raise ValueError("Expected a bounded task and explicit output paths")
        allowed = sorted(set(safe_path(name) for name in allowed))
        reviewer = review_gateway or gateway
        directory = self.directory(key)
        agents = AgentRun(directory / "agents", image=self.image)
        if not agents.image_configured:
            raise ValueError("Configure an immutable Flue agent image")
        request = {
            "source": str(source.resolve()),
            "task": task,
            "allowed": allowed,
            "recipe": recipe.model_dump(),
            "runtime": AGENT_VERSION,
            "image": agents.docker.image,
            "coding_profile": gateway.identity_sha256,
            "review_profile": reviewer.identity_sha256,
            "timeout": timeout,
            "max_requests": max_requests,
            "max_repairs": max_repairs,
        }
        with lock(directory / "control.lock"):
            path = directory / "job.json"
            if path.exists():
                record = json.loads(path.read_bytes())
                if record["request"] != request:
                    raise ValueError("Workflow key already has a different request")
                if record.get("key") is None:
                    record["key"] = key
                    atomic_write(path, canonical(record))
                if record["state"] in ("ready_local", "cancelled"):
                    return self.inspect(key)
            else:
                workspace = directory / "snapshots" / str(uuid.uuid4())
                base = snapshot(source, workspace)
                record = {
                    "schema_version": "development-workflow/v1",
                    "key": key,
                    "request": request,
                    "state": "queued",
                    "base": base,
                    "workspace": str(workspace),
                    "deadline": time.time() + timeout,
                    "steps": {},
                    "events": [],
                    "candidates": [],
                    "active": None,
                }
                self.save(directory, record, "workflow.created")
            bench = Workbench(directory / "evidence")

            def guard():
                if (directory / "cancel").exists():
                    raise ValueError("Workflow cancellation requested")
                if time.time() >= record["deadline"]:
                    raise ValueError("Workflow deadline exhausted")

            def step(name, state, operation, *, cap=0, active=None):
                with lock(directory / "dispatch.lock", timeout=15):
                    guard()
                    previous = record["steps"].get(name)
                    if previous and "result" in previous:
                        return previous["result"]
                    if previous is None:
                        allowance = min(cap, max_requests - self.spent(record))
                        if cap and allowance < 1:
                            raise ValueError("Workflow model request budget exhausted")
                        previous = {"reserved_requests": allowance}
                        record["steps"][name] = previous
                        event = name + ".intent"
                    else:
                        event = name + ".resuming"
                    record["state"], record["active"] = state, active
                    self.save(directory, record, event)
                result = operation(previous["reserved_requests"])
                previous["result"] = result
                record["active"] = None
                self.save(directory, record, name + ".completed")
                return result

            files = record["base"]["files"]
            feedback = ""
            try:
                for attempt in range(max_repairs + 1):
                    agent_key = f"coder-{attempt}"
                    result = step(
                        agent_key,
                        "implementing" if attempt == 0 else "repairing",
                        lambda cap: agents.run(
                            agent_key,
                            files,
                            task + feedback,
                            allowed,
                            gateway,
                            timeout=180,
                            max_requests=cap,
                            absolute_deadline=record["deadline"],
                        ),
                        cap=12,
                        active={"kind": "agent", "key": agent_key},
                    )

                    def register(_):
                        workspace = directory / "candidates" / str(uuid.uuid4())
                        shutil.copytree(record["workspace"], workspace)
                        for name, value in result["files"].items():
                            target = workspace / name
                            if value is None:
                                target.unlink(missing_ok=True)
                            else:
                                atomic_write(target, base64.b64decode(value["data"], validate=True))
                                target.chmod(value["mode"])
                        sealed = bench.register(
                            source, workspace, record["base"], allowed, recipe, digest(key.encode())
                        )
                        sha = sealed["candidate"]
                        if attempt and sha in record["candidates"][:attempt]:
                            raise ValueError("Repair repeated an earlier candidate")
                        lineage_path = (
                            bench.root / "workflows" / digest(key.encode()) / "lineage.json"
                        )
                        with lock(lineage_path.parent / "control.lock"):
                            lineage = json.loads(lineage_path.read_bytes())
                            expected = record["candidates"][:attempt] + [sha]
                            if lineage["candidates"] not in (expected, expected[:-1]):
                                raise ValueError("Candidate lineage changed")
                            lineage["candidates"] = expected
                            atomic_write(lineage_path, canonical(lineage))
                        record["candidates"] = expected
                        return sealed

                    candidate = step(f"candidate-{attempt}", "sealing", register)
                    sha = candidate["candidate"]
                    checked = step(
                        f"verify-{attempt}",
                        "verifying",
                        lambda _: bench.verify(sha),
                        active={"kind": "verify", "key": sha},
                    )
                    clear = False
                    if checked["passed"]:
                        diff = bench.artifacts.get(
                            load_candidate(bench.artifacts, sha)["patch_sha256"]
                        )
                        review_key = f"reviewer-{attempt}"
                        reviewed = step(
                            review_key,
                            "reviewing",
                            lambda cap: agents.run(
                                review_key,
                                {},
                                "Review this exact patch. input_sha256: "
                                + digest(diff)
                                + "\n\n"
                                + diff.decode(),
                                [],
                                reviewer,
                                role="reviewer",
                                timeout=120,
                                max_requests=cap,
                                absolute_deadline=record["deadline"],
                            ),
                            cap=2,
                            active={"kind": "agent", "key": review_key},
                        )
                        verdict = validate(reviewed["message"].encode(), diff)
                        report = {
                            "candidate": sha,
                            "verdict": verdict.model_dump(),
                            "agent_execution": reviewed["execution_id"],
                            "usage": reviewed["usage"],
                            "model_requests": reviewed["model_requests"],
                            "cost_usd": None,
                        }
                        atomic_write(bench.root / sha / "review.json", canonical(report))
                        clear = not verdict.blocked
                        feedback = (
                            "\n\nReview of the previous attempt (untrusted evidence):\n"
                            + canonical(verdict.model_dump()).decode()[:3500]
                        )
                    else:
                        feedback = (
                            "\n\nPublic checks failed on the previous attempt (untrusted evidence):\n"
                            + bench.artifacts.get(checked["output_sha256"]).decode()[:3500]
                        )
                    if checked["passed"] and clear:
                        with lock(directory / "dispatch.lock", timeout=15):
                            guard()
                            record["state"] = "ready_local"
                            self.save(directory, record, "workflow.ready")
                        return self.inspect(key)
                    files = load_candidate(bench.artifacts, sha)["files"]
                record["state"] = "needs_attention"
                self.save(directory, record, "workflow.repair_budget_exhausted")
            except BaseException as exc:
                record["state"] = "needs_attention"
                record["error_class"] = type(exc).__name__
                if (directory / "cancel").exists():
                    try:
                        self.cancel(key)
                    except BaseException:
                        self.save(directory, record, "workflow.termination_unconfirmed")
                        raise
                    record["state"], record["active"] = "cancelled", None
                    self.save(directory, record, "workflow.cancelled")
                    return self.inspect(key)
                self.save(directory, record, "workflow.interrupted")
                raise
            return self.inspect(key)
