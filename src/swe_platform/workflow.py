"""Durable orchestration of Flue agents and deterministic verification.

The coordinator decides transitions. Agents produce evidence, never authority.
Each job owns its snapshot, stage journal, request budget, and candidate lineage.
"""

import base64
import json
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .broker.host import AGENT_VERSION, AgentRun
from .candidates import Workbench
from .io import atomic_write, canonical, digest, lock
from .policy import AnalysisHandoff, DevelopmentPlan, DevelopmentPolicy
from .review.flue import validate
from .sandbox.docker import Docker, safe_path
from .telemetry import ModelLedger
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
            "policy_sha256": record["request"].get("policy_sha256"),
            "mode": record["request"].get("policy", {}).get("mode", "review"),
            "plan": record.get("plan"),
        }
        if (directory / "model-ledger.json").exists():
            result["accounting"] = ModelLedger(
                directory,
                max_requests=record["request"]["max_requests"],
                max_tokens=record["request"]["max_total_tokens"],
                deadline=record["deadline"],
            ).summary()
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
            if record["state"] in ("ready_local", "verified_local", "cancelled"):
                return self.inspect(key)
            atomic_write(directory / "cancel", b"requested\n")
        active = record.get("active")
        if active and active["kind"] in ("agent", "agents"):
            agent = AgentRun(directory / "agents", image=record["request"]["image"])
            keys = active.get("keys", [active.get("key")])
            for active_key in keys:
                target = directory / "agents" / digest(active_key.encode())
                # A tombstone also covers cancellation between job intent and agent launch.
                atomic_write(target / "cancel", b"requested\n")
            failures = []
            for active_key in keys:
                target = directory / "agents" / digest(active_key.encode())
                if (target / "intent.json").exists():
                    try:
                        agent.cancel(active_key)
                    except Exception as exc:
                        failures.append(type(exc).__name__)
            if failures:
                raise ValueError("Agent group termination is unconfirmed")
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
                if record["state"] not in ("ready_local", "verified_local", "cancelled"):
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
        max_total_tokens=None,
        policy=None,
        trial_ticket_sha256=None,
        execution_id=None,
        expected_base_revision=None,
    ):
        if not 1 <= timeout <= 3600 or not 1 <= max_requests <= 90 or not 0 <= max_repairs <= 2:
            raise ValueError("Invalid workflow budget")
        if not 1 <= len(task.encode()) <= 12000 or not allowed:
            raise ValueError("Expected a bounded task and explicit output paths")
        allowed = sorted(set(safe_path(name) for name in allowed))
        reviewer = review_gateway or gateway
        selected_policy = policy or DevelopmentPolicy()
        token_limit = max_total_tokens if max_total_tokens is not None else 250000
        if not 1 <= token_limit <= 1000000:
            raise ValueError("Invalid total token budget")
        if bool(trial_ticket_sha256) != bool(execution_id):
            raise ValueError("Trial ticket and execution identity must be supplied together")
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
            "max_total_tokens": token_limit,
            "policy": selected_policy.model_dump(),
            "policy_sha256": selected_policy.sha256,
            "trial_ticket_sha256": trial_ticket_sha256,
            "execution_id": execution_id,
            "expected_base_revision": expected_base_revision,
        }
        with lock(directory / "control.lock"):
            path = directory / "job.json"
            if path.exists():
                record = json.loads(path.read_bytes())
                # Existing v1 jobs retain their original policy and accounting contract.
                if (
                    "policy" not in record["request"]
                    and policy is None
                    and max_total_tokens is None
                    and trial_ticket_sha256 is None
                ):
                    for field in (
                        "max_total_tokens",
                        "policy",
                        "policy_sha256",
                        "trial_ticket_sha256",
                        "execution_id",
                        "expected_base_revision",
                    ):
                        request.pop(field)
                if record["request"] != request:
                    raise ValueError("Workflow key already has a different request")
                if record.get("key") is None:
                    record["key"] = key
                    atomic_write(path, canonical(record))
                if record["state"] in ("ready_local", "verified_local", "cancelled"):
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
            if (
                expected_base_revision is not None
                and record["base"]["base_revision"] != expected_base_revision
            ):
                raise ValueError("Source snapshot differs from the reserved trial base")
            bench = Workbench(directory / "evidence")
            ledger = (
                ModelLedger(
                    directory,
                    max_requests=max_requests,
                    max_tokens=token_limit,
                    deadline=record["deadline"],
                )
                if "max_total_tokens" in request
                else None
            )

            def metered(target, stage, role):
                return ledger.stage(target, stage, role) if ledger else target

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
                planning = ""
                if selected_policy.mode == "selective":
                    planned = step(
                        "planner",
                        "planning",
                        lambda cap: agents.run(
                            "planner",
                            files,
                            task
                            + "\nPlanning contract: "
                            + canonical(
                                {
                                    "snapshot_sha256": digest(canonical(files)),
                                    "allowed_paths": allowed,
                                    "max_specialists": selected_policy.max_specialists,
                                }
                            ).decode(),
                            [],
                            metered(gateway, "planner", "planner"),
                            role="planner",
                            max_requests=cap,
                            absolute_deadline=record["deadline"],
                        ),
                        cap=selected_policy.planner_requests,
                        active={"kind": "agent", "key": "planner"},
                    )
                    plan = DevelopmentPlan.model_validate_json(planned["message"]).check(
                        files, allowed, selected_policy.max_specialists
                    )
                    if record.get("plan") != plan.model_dump():
                        record["plan"] = plan.model_dump()
                        self.save(directory, record, "plan.validated")
                    planning = (
                        "\n\nValidated plan (advisory evidence):\n"
                        + canonical(plan.model_dump()).decode()
                    )
                    if plan.specialists:
                        specialist_keys = ["specialist-" + item.id for item in plan.specialists]

                        def analyze(cap):
                            # Each agent owns its receipt; only this thread mutates the workflow.
                            if cap < len(plan.specialists):
                                raise ValueError("Insufficient requests for planned analysis")
                            results = {}

                            def run_specialist(item, allowance):
                                key = "specialist-" + item.id
                                inputs = {name: files[name] for name in item.paths}
                                assignment = {
                                    **item.model_dump(),
                                    "specialist_id": item.id,
                                    "snapshot_sha256": digest(canonical(inputs)),
                                }
                                result = agents.run(
                                    key,
                                    inputs,
                                    task
                                    + "\nAnalysis assignment: "
                                    + canonical(assignment).decode(),
                                    [],
                                    metered(gateway, "analysis", "specialist"),
                                    role="specialist",
                                    max_requests=allowance,
                                    absolute_deadline=record["deadline"],
                                )
                                handoff = AnalysisHandoff.model_validate_json(
                                    result["message"]
                                ).check(item, inputs)
                                return {
                                    "handoff": handoff.model_dump(),
                                    "model_requests": result["model_requests"],
                                    "execution_id": result["execution_id"],
                                }

                            with ThreadPoolExecutor(max_workers=2) as pool:
                                futures = {
                                    pool.submit(
                                        run_specialist,
                                        item,
                                        cap // len(plan.specialists)
                                        + (i < cap % len(plan.specialists)),
                                    ): item.id
                                    for i, item in enumerate(plan.specialists)
                                }
                                try:
                                    for future in as_completed(futures):
                                        results[futures[future]] = future.result()
                                except BaseException:
                                    for agent_key in specialist_keys:
                                        target = directory / "agents" / digest(agent_key.encode())
                                        atomic_write(
                                            target / "cancel", b"analysis sibling failed\n"
                                        )
                                        if (target / "intent.json").exists() and not (
                                            target / "result.json"
                                        ).exists():
                                            agents.cancel(agent_key)
                                    raise
                            return {
                                "handoffs": results,
                                "model_requests": sum(
                                    value["model_requests"] for value in results.values()
                                ),
                            }

                        analyses = step(
                            "analysis",
                            "analyzing",
                            analyze,
                            cap=selected_policy.specialist_requests * len(plan.specialists),
                            active={"kind": "agents", "keys": specialist_keys},
                        )
                        planning += (
                            "\n\nRead-only handoffs (advisory evidence):\n"
                            + canonical(
                                {
                                    key: value["handoff"]
                                    for key, value in analyses["handoffs"].items()
                                }
                            ).decode()
                        )
                coding_task = task + (
                    "\n\nProduction guidance:\n" + selected_policy.coding_guidance
                    if selected_policy.coding_guidance
                    else ""
                )
                for attempt in range(max_repairs + 1):
                    agent_key = f"coder-{attempt}"
                    result = step(
                        agent_key,
                        "implementing" if attempt == 0 else "repairing",
                        lambda cap: agents.run(
                            agent_key,
                            files,
                            coding_task + planning + feedback,
                            allowed,
                            metered(gateway, agent_key, "coder"),
                            timeout=180,
                            max_requests=cap,
                            absolute_deadline=record["deadline"],
                        ),
                        cap=selected_policy.coder_requests,
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
                    if checked["passed"] and selected_policy.mode == "single":
                        with lock(directory / "dispatch.lock", timeout=15):
                            guard()
                            record["state"] = "verified_local"
                            self.save(directory, record, "workflow.verified")
                        return self.inspect(key)
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
                                metered(reviewer, review_key, "reviewer"),
                                role="reviewer",
                                timeout=120,
                                max_requests=cap,
                                absolute_deadline=record["deadline"],
                            ),
                            cap=selected_policy.reviewer_requests,
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
