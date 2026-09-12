"""Exact-candidate GitHub publication with durable intent and remote reconciliation."""

import base64
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .candidates import Workbench
from .github import GitHub
from .io import atomic_write, canonical, digest, lock
from .models import StrictModel
from .sandbox.docker import safe_path
from .workspace.snapshot import load_candidate


def object_sha(kind, data):
    return hashlib.sha1(kind.encode() + b" " + str(len(data)).encode() + b"\0" + data).hexdigest()


def git_tree(files):
    """Compute Git's recursive tree identity without a checkout or repository hooks."""
    root, entries, blobs = {}, {}, {}
    for path, value in files.items():
        safe_path(path)
        if value["mode"] not in (0o644, 0o755):
            raise ValueError("Invalid candidate file mode")
        data = base64.b64decode(value["data"], validate=True)
        sha = object_sha("blob", data)
        blobs[sha] = value["data"]
        entry = {"sha": sha, "mode": f"100{value['mode']:o}"}
        entries[path] = entry
        folder = root
        parts = path.split("/")
        for part in parts[:-1]:
            folder = folder.setdefault(part, {})
        if parts[-1] in folder:
            raise ValueError("Conflicting candidate paths")
        folder[parts[-1]] = (entry["mode"], sha)

    def tree(folder):
        children = []
        for name, item in folder.items():
            directory = isinstance(item, dict)
            mode, sha = ("40000", tree(item)) if directory else item
            key = name.encode() + (b"/" if directory else b"")
            children.append(
                (key, mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(sha))
            )
        return object_sha("tree", b"".join(data for _, data in sorted(children)))

    return tree(root), entries, blobs


class GitHubPlan(StrictModel):
    schema_version: Literal["github-publication-plan/v1"] = "github-publication-plan/v1"
    repository: str = Field(pattern=r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
    repository_id: int = Field(gt=0)
    actor_id: int = Field(gt=0)
    actor_login: str = Field(pattern=r"^[a-zA-Z0-9-]+$")
    base_branch: str
    branch: str
    base_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    head_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    tree_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    candidate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    patch_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(max_length=16000)
    author_name: str = Field(min_length=1, max_length=200)
    author_email: str = Field(min_length=1, max_length=254)
    created_at: int = Field(gt=0)
    expires_at: int = Field(gt=0)

    @field_validator("branch", "base_branch")
    @classmethod
    def branch_name(cls, name):
        if (
            not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/-]{0,199}", name)
            or any(part in name for part in ("..", "//"))
            or any(
                part.startswith(".") or part.endswith((".", ".lock")) for part in name.split("/")
            )
            or name.endswith("/")
        ):
            raise ValueError("Invalid Git branch name")
        return name

    @field_validator("title", "author_name", "author_email")
    @classmethod
    def single_line(cls, value):
        if not value.strip() or any(ord(c) < 32 or c in "<>" for c in value):
            raise ValueError("Expected a nonblank single-line value")
        return value

    @model_validator(mode="after")
    def consistent(self):
        if self.branch == self.base_branch or not self.created_at < self.expires_at:
            raise ValueError("Publication needs a new branch and a valid expiry")
        if object_sha("commit", self.commit_bytes()) != self.head_sha:
            raise ValueError("Publication commit identity mismatch")
        return self

    def commit_bytes(self):
        identity = f"{self.author_name} <{self.author_email}> {self.created_at} +0000"
        return (
            f"tree {self.tree_sha}\nparent {self.base_sha}\nauthor {identity}\n"
            f"committer {identity}\n\n{self.title}\n"
        ).encode()

    def commit_payload(self):
        author = {
            "name": self.author_name,
            "email": self.author_email,
            "date": datetime.fromtimestamp(self.created_at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        return {
            "tree": self.tree_sha,
            "parents": [self.base_sha],
            "message": self.title + "\n",
            "author": author,
            "committer": author,
        }

    @property
    def sha256(self):
        return digest(canonical(self.model_dump()))

    @property
    def marker(self):
        return f"<!-- publication:{self.sha256} -->"


class Publisher:
    def __init__(self, root: Path, github=None, *, fault=lambda _: None):
        self.root = root / "publications"
        self.bench = Workbench(root)
        self.github = github or GitHub()
        self.fault = fault

    def directory(self, sha):
        if not re.fullmatch(r"[a-f0-9]{64}", sha):
            raise ValueError("Invalid publication digest")
        return self.root / sha

    def load(self, sha):
        plan = GitHubPlan.model_validate_json((self.directory(sha) / "plan.json").read_bytes())
        if plan.sha256 != sha:
            raise ValueError("Publication plan changed")
        return plan

    def inspect(self, sha):
        plan = self.load(sha)
        path = self.directory(sha) / "journal.json"
        record = (
            json.loads(path.read_bytes()) if path.exists() else {"state": "prepared", "events": []}
        )
        return {"plan_sha256": sha, "plan": plan.model_dump(), **record}

    def evidence(self, sha):
        inspected = self.bench.inspect(sha)
        if inspected["state"] != "ready_local":
            raise ValueError(
                "Publication requires the current candidate with passing checks and review"
            )
        policy = self.bench.policy(sha)
        value = {
            "candidate": sha,
            "recipe": policy["recipe"],
            "allowed": policy["allowed_paths"],
            "evidence": inspected["evidence"],
        }
        return digest(canonical(value))

    def prepare(self, sha, repository, *, branch, title, body, base_branch=None, lifetime=86400):
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", repository):
            raise ValueError("Expected owner/repository")
        GitHubPlan.branch_name(branch)
        if not 60 <= lifetime <= 86400:
            raise ValueError("Publication plan lifetime must be 60 to 86400 seconds")
        evidence = self.evidence(sha)
        candidate = load_candidate(self.bench.artifacts, sha)
        if not candidate["changed_paths"]:
            raise ValueError("Candidate has no changes")
        remote = self.github.repository(repository)
        repository = remote["full_name"]
        actor = self.github.identity()
        base_branch = GitHubPlan.branch_name(base_branch or remote["default_branch"])
        if self.github.branch(repository, base_branch) != candidate["base_revision"]:
            raise ValueError("Remote base advanced; verify a candidate against the current base")
        if self.github.branch(repository, branch) is not None:
            raise ValueError("Publication branch already exists; choose a new branch")
        tree, _, _ = git_tree(candidate["files"])
        created = int(time.time())
        values = dict(
            repository=repository,
            repository_id=remote["id"],
            actor_id=actor["id"],
            actor_login=actor["login"],
            base_branch=base_branch,
            branch=branch,
            base_sha=candidate["base_revision"],
            tree_sha=tree,
            candidate_sha256=sha,
            evidence_sha256=evidence,
            patch_sha256=candidate["patch_sha256"],
            title=title,
            body=body,
            author_name=actor.get("name") or actor["login"],
            author_email=f"{actor['id']}+{actor['login']}@users.noreply.github.com",
            created_at=created,
            expires_at=created + lifetime,
        )
        provisional = GitHubPlan.model_construct(**values, head_sha="0" * 40)
        plan = GitHubPlan(**values, head_sha=object_sha("commit", provisional.commit_bytes()))
        with lock(self.directory(plan.sha256) / "control.lock"):
            atomic_write(self.directory(plan.sha256) / "plan.json", canonical(plan.model_dump()))
        return self.inspect(plan.sha256)

    def save(self, sha, record, event):
        record["events"].append(
            {"seq": len(record["events"]) + 1, "at": time.time(), "event": event}
        )
        atomic_write(self.directory(sha) / "journal.json", canonical(record))

    def guard(self, plan):
        if self.evidence(plan.candidate_sha256) != plan.evidence_sha256:
            raise ValueError("Publication evidence changed; prepare a new plan")
        if self.github.repository(plan.repository)["id"] != plan.repository_id:
            raise ValueError("GitHub repository identity changed")
        if self.github.identity()["id"] != plan.actor_id:
            raise ValueError("GitHub operator identity changed")
        if self.github.branch(plan.repository, plan.base_branch) != plan.base_sha:
            raise ValueError("Remote base advanced; publication evidence is stale")
        head = self.github.branch(plan.repository, plan.branch)
        if head is not None and head != plan.head_sha:
            raise ValueError("Remote branch has different content; it will not be overwritten")

    def ensure_objects(self, plan, candidate):
        repository = plan.repository
        tree, entries, blobs = git_tree(candidate["files"])
        if tree != plan.tree_sha:
            raise ValueError("Candidate tree changed")
        if self.github.object(repository, "commits", plan.head_sha) is not None:
            self.verify_commit(plan, entries)
            return
        if self.github.object(repository, "trees", tree) is None:
            base = self.github.object(repository, "commits", plan.base_sha)
            if base is None:
                raise ValueError("Remote base commit is missing")
            base_entries = self.github.tree(repository, base["tree"]["sha"])
            known_blobs = {item["sha"] for item in base_entries.values()}
            for sha, encoded in blobs.items():
                if sha not in known_blobs:
                    result = self.github.create_object(
                        repository, "blobs", {"content": encoded, "encoding": "base64"}
                    )
                    if result["sha"] != sha:
                        raise ValueError("GitHub blob differs from the sealed candidate")
            changed = [
                {"path": path, "type": "blob", **entry}
                for path, entry in entries.items()
                if base_entries.get(path) != entry
            ]
            changed += [
                {"path": path, "type": "blob", "mode": item["mode"], "sha": None}
                for path, item in base_entries.items()
                if path not in entries
            ]
            result = self.github.create_object(
                repository, "trees", {"base_tree": base["tree"]["sha"], "tree": changed}
            )
            if result["sha"] != tree:
                raise ValueError("GitHub tree differs from the sealed candidate")
        result = self.github.create_object(repository, "commits", plan.commit_payload())
        if result["sha"] != plan.head_sha:
            raise ValueError("GitHub commit differs from the approved plan")
        self.verify_commit(plan, entries)

    def verify_commit(self, plan, entries=None):
        commit = self.github.object(plan.repository, "commits", plan.head_sha)
        if (
            commit is None
            or commit["tree"]["sha"] != plan.tree_sha
            or [p["sha"] for p in commit["parents"]] != [plan.base_sha]
        ):
            raise ValueError("Remote commit does not match the publication plan")
        if entries is None:
            candidate = load_candidate(self.bench.artifacts, plan.candidate_sha256)
            _, entries, _ = git_tree(candidate["files"])
        if self.github.tree(plan.repository, plan.tree_sha) != entries:
            raise ValueError("Remote files or modes differ from the sealed candidate")

    def match_pull(self, plan, pull):
        expected = (
            plan.repository_id,
            plan.branch,
            plan.head_sha,
            plan.repository_id,
            plan.base_branch,
            plan.base_sha,
            plan.actor_id,
        )
        actual = (
            pull["head"]["repo"]["id"] if pull["head"].get("repo") else None,
            pull["head"]["ref"],
            pull["head"]["sha"],
            pull["base"]["repo"]["id"],
            pull["base"]["ref"],
            pull["base"]["sha"],
            pull["user"]["id"],
        )
        if actual != expected or plan.marker not in (pull.get("body") or ""):
            raise ValueError("Remote pull request identity or revisions changed")
        if pull["state"] != "open" or not pull["draft"]:
            raise ValueError("Remote pull request is no longer an open draft")

    def publish(self, sha, *, reconcile_only=False):
        """Calling publish explicitly approves this immutable plan; reconciliation only reads GitHub."""
        plan = self.load(sha)
        policy = self.bench.policy(plan.candidate_sha256)
        with (
            lock(self.directory(sha) / "control.lock"),
            lock(self.bench.root / "workflows" / policy["workflow_id"] / "control.lock"),
        ):
            path = self.directory(sha) / "journal.json"
            record = (
                json.loads(path.read_bytes())
                if path.exists()
                else {"state": "prepared", "events": [], "effects": {}, "pull_request": None}
            )
            try:
                self.guard(plan)
                if not reconcile_only:
                    if time.time() >= plan.expires_at:
                        if record["effects"].get("pull_request") in ("in_flight", "confirmed"):
                            reconcile_only = True
                        else:
                            raise ValueError("Publication plan expired; prepare a new plan")
                    if not record.get("approved_at"):
                        record["approved_at"] = time.time()
                        self.save(sha, record, "publication.approved")
                candidate = load_candidate(self.bench.artifacts, plan.candidate_sha256)
                if not reconcile_only:
                    self.ensure_objects(plan, candidate)
                    self.guard(plan)
                head = self.github.branch(plan.repository, plan.branch)
                if head is None:
                    if reconcile_only:
                        record["state"] = "awaiting_branch" if record["effects"] else "prepared"
                        self.save(sha, record, "publication.reconciled")
                        return self.inspect(sha)
                    record["effects"]["branch"] = "in_flight"
                    self.save(sha, record, "branch.intent")
                    self.fault("before_branch")
                    # The branch name is a unique remote key. Creation never updates an existing ref.
                    self.github.create_branch(plan.repository, plan.branch, plan.head_sha)
                    self.fault("after_branch")
                    if self.github.branch(plan.repository, plan.branch) != plan.head_sha:
                        raise ValueError("Remote branch creation is not confirmed")
                self.verify_commit(plan)
                record["effects"]["branch"] = "confirmed"
                self.save(sha, record, "branch.confirmed")
                pulls = self.github.pulls(plan.repository, plan.branch)
                matches = [p for p in pulls if plan.marker in (p.get("body") or "")]
                if len(matches) > 1:
                    raise ValueError("Multiple matching pull requests require reconciliation")
                if matches:
                    pull = self.github.pull(plan.repository, matches[0]["number"])
                elif pulls:
                    raise ValueError("Publication branch already has a different pull request")
                elif record["effects"].get("pull_request") in ("in_flight", "confirmed"):
                    record["state"] = "ambiguous"
                    self.save(sha, record, "pull_request.unconfirmed")
                    return self.inspect(sha)
                elif reconcile_only:
                    record["state"] = "awaiting_pull_request"
                    self.save(sha, record, "publication.reconciled")
                    return self.inspect(sha)
                else:
                    if time.time() >= plan.expires_at:
                        raise ValueError("Publication plan expired before pull request dispatch")
                    self.guard(plan)
                    record["effects"]["pull_request"] = "in_flight"
                    self.save(sha, record, "pull_request.intent")
                    self.fault("before_pull")
                    created = self.github.create_pull(
                        plan.repository,
                        {
                            "head": plan.branch,
                            "base": plan.base_branch,
                            "title": plan.title,
                            "body": plan.body + "\n\n" + plan.marker,
                            "draft": True,
                            "maintainer_can_modify": False,
                        },
                    )
                    self.fault("after_pull")
                    pull = self.github.pull(plan.repository, created["number"])
                self.match_pull(plan, pull)
                self.guard(plan)
                record["effects"]["pull_request"] = "confirmed"
                record["pull_request"] = {
                    "number": pull["number"],
                    "url": pull["html_url"],
                    "head_sha": plan.head_sha,
                    "draft": True,
                }
                record["state"] = "published"
                record.pop("error", None)
                self.save(sha, record, "publication.confirmed")
                record["checks"] = self.github.checks(plan.repository, plan.head_sha)
                self.save(sha, record, "checks.observed")
                return self.inspect(sha)
            except Exception as exc:
                record["state"] = "needs_attention"
                record["error"] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                self.save(sha, record, "publication.interrupted")
                raise
