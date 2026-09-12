"""Candidate intake and durable, exact-content verification."""

import json
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import Field

from .credentials import GatewayProfile
from .io import Artifacts, atomic_write, canonical, digest, lock
from .models import StrictModel
from .review.flue import review, validate
from .sandbox.docker import Docker
from .workspace.snapshot import git, load_candidate, seal, snapshot


class Recipe(StrictModel):
    schema_version: Literal["verification-recipe/v1"] = "verification-recipe/v1"
    image: str
    argv: list[str] = Field(min_length=1, max_length=100)
    timeout: float = Field(default=60, gt=0, le=1200)


class Workbench:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        self.root = root / "candidates"
        self.artifacts = Artifacts(root / "artifacts")

    def intake(
        self, source: Path, patch: bytes, allowed: list[str], recipe: Recipe, *, _workflow=None
    ):
        if not 0 < len(patch) <= 1024 * 1024:
            raise ValueError("Patch must be 1 byte to 1 MiB")
        Docker(self.root, image=recipe.image)  # Validate immutable image before creating state.
        work_id = str(uuid.uuid4())
        workflow = _workflow or work_id
        directory = self.root / "intake" / work_id
        base = snapshot(source, directory / "repo")
        git(directory / "repo", "apply", "--check", "-", data=patch)
        git(directory / "repo", "apply", "-", data=patch)
        return self.register(source, directory / "repo", base, allowed, recipe, workflow)

    def register(self, source, workspace, base, allowed, recipe, workflow):
        """Seal trusted-host materialized output; this workspace is never a worker mount."""
        sha, candidate = seal(workspace, self.artifacts, base["base_revision"], allowed)
        target = self.root / sha
        with lock(target / "control.lock"):
            if (target / "policy.json").exists():
                previous = json.loads((target / "policy.json").read_bytes())
                if previous["recipe"] != recipe.model_dump():
                    raise ValueError("Existing candidate has a different trusted recipe")
                workflow = previous["workflow_id"]
            else:
                policy = {
                    "candidate": sha,
                    "recipe": recipe.model_dump(),
                    "allowed_paths": allowed,
                    "base_revision": base["base_revision"],
                    "workspace": str(workspace),
                    "created": time.time(),
                    "review_required": True,
                    "workflow_id": workflow,
                    "source": str(source.resolve()),
                }
                atomic_write(target / "policy.json", canonical(policy))
            lineage = self.root / "workflows" / workflow / "lineage.json"
            if not lineage.exists():
                atomic_write(lineage, canonical({"candidates": [sha], "max_repairs": 2}))
        return {
            "candidate": sha,
            "changed_paths": candidate["changed_paths"],
            "patch": str(self.artifacts.root / candidate["patch_sha256"]),
        }

    def repair(self, sha, patch: bytes):
        policy = self.policy(sha)
        journal = self.root / "workflows" / policy["workflow_id"]
        with lock(journal / "control.lock"):
            lineage = json.loads((journal / "lineage.json").read_bytes())
            if lineage["candidates"][-1] != sha:
                raise ValueError("Repair must start from the current candidate")
            if len(lineage["candidates"]) - 1 >= lineage["max_repairs"]:
                raise ValueError("Repair allowance exhausted")
            source = Path(policy["source"])
            if git(source, "rev-parse", "HEAD").decode().strip() != policy["base_revision"]:
                raise ValueError("Source revision changed; start a separate task")
            result = self.intake(
                source,
                patch,
                policy["allowed_paths"],
                Recipe.model_validate(policy["recipe"]),
                _workflow=policy["workflow_id"],
            )
            if result["candidate"] in lineage["candidates"]:
                raise ValueError("Repair produced an identical or repeated candidate")
            child = self.policy(result["candidate"])
            if child["workflow_id"] != policy["workflow_id"]:
                raise ValueError("Candidate already belongs to another workflow")
            lineage["candidates"].append(result["candidate"])
            # The candidate and its policy exist before the atomic lineage pointer advances.
            atomic_write(journal / "lineage.json", canonical(lineage))
            return result

    def policy(self, sha):
        load_candidate(self.artifacts, sha)
        return json.loads((self.root / sha / "policy.json").read_bytes())

    def verify(self, sha):
        policy = self.policy(sha)
        recipe = Recipe.model_validate(policy["recipe"])
        candidate = load_candidate(self.artifacts, sha)
        directory = self.root / sha
        lineage = json.loads(
            (self.root / "workflows" / policy["workflow_id"] / "lineage.json").read_bytes()
        )
        if lineage["candidates"][-1] != sha:
            raise ValueError("Cannot verify a superseded candidate")
        with lock(directory / "control.lock"):
            path = directory / "verification.json"
            if path.exists():
                return json.loads(path.read_bytes())
            request = {"candidate": sha, "recipe_sha256": digest(canonical(recipe.model_dump()))}
            atomic_write(directory / "verification-intent.json", canonical(request))
            docker = Docker(self.root / "containers", image=recipe.image)
            result = docker.run(sha, candidate["files"], recipe.argv, timeout=recipe.timeout)
            report = {
                **request,
                "passed": result["exit_code"] == 0 and result["reason"] is None,
                "exit_code": result["exit_code"],
                "reason": result["reason"],
                "output_sha256": self.artifacts.put(result["output"].encode()),
            }
            atomic_write(path, canonical(report))
            # Removal only follows a durable receipt. Interrupted commands retain their container.
            docker.remove(sha)
            return report

    def review(self, sha, cli: Path, profile: GatewayProfile, node: str):
        self.policy(sha)
        candidate = load_candidate(self.artifacts, sha)
        directory = self.root / sha
        with lock(directory / "control.lock"):
            path = directory / "review.json"
            if path.exists():
                return json.loads(path.read_bytes())
            intent = directory / "review-intent.json"
            if intent.exists():
                raise ValueError(
                    "Prior review has no receipt; reconcile before another billable call"
                )
            # Resolve credentials before committing intent, without persisting their values.
            environment = profile.environment()
            atomic_write(intent, canonical({"candidate": sha, "started": time.time()}))
            verdict = review(
                cli,
                self.artifacts.get(candidate["patch_sha256"]),
                node=node,
                model_environment=environment,
            )
            report = {"candidate": sha, "verdict": verdict.model_dump(), "cost_usd": None}
            atomic_write(path, canonical(report))
            return report

    def inspect(self, sha):
        policy = self.policy(sha)
        lineage = json.loads(
            (self.root / "workflows" / policy["workflow_id"] / "lineage.json").read_bytes()
        )
        candidate = load_candidate(self.artifacts, sha)
        directory = self.root / sha
        evidence = {}
        for name in ("verification", "review"):
            path = directory / (name + ".json")
            if path.exists():
                report = json.loads(path.read_bytes())
                if report["candidate"] != sha:
                    raise ValueError("Evidence does not match the candidate")
                evidence[name] = report
        if "verification" in evidence:
            expected = digest(canonical(policy["recipe"]))
            if evidence["verification"]["recipe_sha256"] != expected:
                raise ValueError("Verification recipe changed")
            self.artifacts.get(evidence["verification"]["output_sha256"])
        if "review" in evidence:
            validate(
                canonical(evidence["review"]["verdict"]),
                self.artifacts.get(candidate["patch_sha256"]),
            )
        ready = (
            evidence.get("verification", {}).get("passed")
            and "review" in evidence
            and not evidence["review"]["verdict"]["blocked"]
        )
        return {
            "candidate": sha,
            "state": "stale"
            if lineage["candidates"][-1] != sha
            else "ready_local"
            if ready
            else "needs_attention",
            "repairs_used": len(lineage["candidates"]) - 1,
            "changed_paths": candidate["changed_paths"],
            "evidence": evidence,
            "patch": str(self.artifacts.root / candidate["patch_sha256"]),
            "independent_evaluation": "not_requested",
            "publication": "not_authorized",
        }

    def review_agent(self, sha, gateway, *, image=None, timeout=120, max_requests=2):
        """Run this platform's Flue reviewer through the same isolated agent boundary."""
        from .broker.host import AGENT_VERSION, AgentRun

        policy = self.policy(sha)
        candidate = load_candidate(self.artifacts, sha)
        directory = self.root / sha
        agent = AgentRun(self.root / "review-agents", image=image)
        spec = {
            "candidate": sha,
            "profile_sha256": gateway.identity_sha256,
            "runtime": AGENT_VERSION,
            "image": agent.docker.image,
            "timeout": timeout,
            "max_requests": max_requests,
        }
        request_sha = digest(canonical(spec))
        lineage = json.loads(
            (self.root / "workflows" / policy["workflow_id"] / "lineage.json").read_bytes()
        )
        if lineage["candidates"][-1] != sha:
            raise ValueError("Cannot review a superseded candidate")
        with lock(directory / "control.lock"):
            path = directory / "review.json"
            if path.exists():
                saved = json.loads(path.read_bytes())
                if saved.get("request_sha256") != request_sha:
                    raise ValueError("Existing review uses a different runtime or policy")
                validate(canonical(saved["verdict"]), self.artifacts.get(candidate["patch_sha256"]))
                return saved
            intent = directory / "review-intent.json"
            if (
                intent.exists()
                and json.loads(intent.read_bytes()).get("request_sha256") != request_sha
            ):
                raise ValueError(
                    "Review intent has a different policy; reconcile it before retrying"
                )
            atomic_write(intent, canonical({**spec, "request_sha256": request_sha}))
            diff = self.artifacts.get(candidate["patch_sha256"])
            result = agent.run(
                request_sha,
                {},
                "Review this exact patch. input_sha256: " + digest(diff) + "\n\n" + diff.decode(),
                [],
                gateway,
                role="reviewer",
                timeout=timeout,
                max_requests=max_requests,
            )
            verdict = validate(result["message"].encode(), diff)
            report = {
                "candidate": sha,
                "request_sha256": request_sha,
                "verdict": verdict.model_dump(),
                "agent_execution": result["execution_id"],
                "model_requests": result["model_requests"],
                "usage": result["usage"],
                "cost_usd": None,
            }
            atomic_write(path, canonical(report))
            return report
