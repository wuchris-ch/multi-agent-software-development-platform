"""Compose one credential-isolated coding run with immutable candidate intake."""

import base64
import json
import uuid
from pathlib import Path

from .broker.host import AgentRun
from .candidates import Workbench
from .io import atomic_write, canonical, digest, lock
from .sandbox.docker import Docker, safe_path
from .workspace.snapshot import snapshot


class CandidateCoding:
    def __init__(self, root: Path, image=None):
        self.workbench = Workbench(root)
        self.runs = AgentRun(root / "coding", image=image)
        self.root = root / "coding-intake"

    def run(self, key, source, task, allowed, recipe, gateway, *, timeout=180, max_requests=12):
        allowed = sorted(set(allowed))
        if not allowed:
            raise ValueError("At least one allowed output path is required")
        for path in allowed:
            safe_path(path)
        Docker(self.root, image=recipe.image)
        request = {
            "key": key,
            "source": str(source.resolve()),
            "task": task,
            "allowed": allowed,
            "recipe": recipe.model_dump(),
            "profile_sha256": gateway.identity_sha256,
            "image": self.runs.docker.image,
            "timeout": timeout,
            "max_requests": max_requests,
        }
        sha = digest(canonical(request))
        directory = self.root / digest(key.encode())
        with lock(directory / "control.lock"):
            intent = directory / "intent.json"
            if intent.exists():
                saved = json.loads(intent.read_bytes())
                if saved["request_sha256"] != sha:
                    raise ValueError("Coding submission key already has another payload")
            else:
                workspace = directory / str(uuid.uuid4()) / "repo"
                base = snapshot(source, workspace)
                saved = {"request_sha256": sha, "workspace": str(workspace), "base": base}
                atomic_write(intent, canonical(saved))
            receipt = directory / "candidate.json"
            if receipt.exists():
                result = json.loads(receipt.read_bytes())
                self.workbench.inspect(result["candidate"])
                return result
            result = self.runs.run(
                key,
                saved["base"]["files"],
                task,
                allowed,
                gateway,
                timeout=timeout,
                max_requests=max_requests,
            )
            workspace = Path(saved["workspace"])
            for name, value in result["files"].items():
                path = workspace / name
                if value is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, base64.b64decode(value["data"], validate=True))
                    path.chmod(value["mode"])
            intake = self.workbench.register(
                source, workspace, saved["base"], allowed, recipe, digest(key.encode())
            )
            evidence = {
                **intake,
                "coding_execution": result["execution_id"],
                "model_requests": result["model_requests"],
                "usage": result["usage"],
                "cost_usd": None,
            }
            atomic_write(receipt, canonical(evidence))
            return evidence
