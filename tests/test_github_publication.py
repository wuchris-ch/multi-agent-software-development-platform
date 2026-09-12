import base64
import copy
import json
import subprocess
from datetime import datetime

import pytest
from test_snapshot import repository as repository
from test_workbench import PATCH

from swe_platform.candidates import Recipe, Workbench
from swe_platform.github import GitHub, RemoteError
from swe_platform.github_publication import Publisher, git_tree, object_sha
from swe_platform.io import atomic_write, canonical, digest
from swe_platform.sandbox.docker import PYTHON_IMAGE


class MemoryGitHub:
    """Repository state survives client failures, just as it does across HTTP requests."""

    def __init__(self, source):
        data = (source / "calc.py").read_bytes()
        tree, self.baseline, blobs = git_tree(
            {"calc.py": {"data": base64.b64encode(data).decode(), "mode": 0o644}}
        )
        base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source).decode().strip()
        self.actor = {"id": 10, "login": "maintainer", "name": "Maintainer"}
        self.metadata = {"id": 20, "full_name": "owner/repo", "default_branch": "main"}
        self.refs = {"main": base}
        self.objects = {
            "blobs": blobs,
            "trees": {tree: self.baseline},
            "commits": {base: {"sha": base, "tree": {"sha": tree}, "parents": []}},
        }
        self.records, self.writes = [], []
        self.hidden = False
        self.check_state = "pending"

    def repository(self, _repository):
        return self.metadata

    def identity(self):
        return self.actor

    def branch(self, _repository, branch):
        return self.refs.get(branch)

    def object(self, _repository, kind, sha):
        return self.objects[kind].get(sha)

    def tree(self, _repository, sha):
        return copy.deepcopy(self.objects["trees"][sha])

    def create_object(self, repository, kind, payload):
        self.writes.append(kind)
        if kind == "blobs":
            data = base64.b64decode(payload["content"])
            sha, value = object_sha("blob", data), payload["content"]
        elif kind == "trees":
            value = self.tree(repository, payload["base_tree"])
            for change in payload["tree"]:
                if change["sha"] is None:
                    value.pop(change["path"])
                else:
                    value[change["path"]] = {key: change[key] for key in ("sha", "mode")}
            files = {
                path: {
                    "mode": int(item["mode"], 8) & 0o777,
                    "data": self.objects["blobs"][item["sha"]],
                }
                for path, item in value.items()
            }
            sha, _, _ = git_tree(files)
        else:
            author = payload["author"]
            timestamp = int(datetime.fromisoformat(author["date"]).timestamp())
            identity = f"{author['name']} <{author['email']}> {timestamp} +0000"
            raw = (
                f"tree {payload['tree']}\nparent {payload['parents'][0]}\n"
                f"author {identity}\ncommitter {identity}\n\n{payload['message']}"
            ).encode()
            sha = (
                subprocess.check_output(
                    ["git", "hash-object", "-t", "commit", "--stdin"], input=raw
                )
                .decode()
                .strip()
            )
            value = {
                "sha": sha,
                "tree": {"sha": payload["tree"]},
                "parents": [{"sha": p} for p in payload["parents"]],
            }
        self.objects[kind][sha] = value
        return {"sha": sha}

    def create_branch(self, _repository, branch, sha):
        self.writes.append("branch")
        if branch in self.refs:
            raise RemoteError(422)
        self.refs[branch] = sha

    def pulls(self, _repository, branch):
        return [] if self.hidden else [p for p in self.records if p["head"]["ref"] == branch]

    def pull(self, _repository, number):
        return copy.deepcopy(self.records[number - 1])

    def create_pull(self, _repository, payload):
        self.writes.append("pull")
        number = len(self.records) + 1
        result = {
            **payload,
            "number": number,
            "state": "open",
            "user": self.actor.copy(),
            "html_url": f"https://github.com/owner/repo/pull/{number}",
            "head": {
                "ref": payload["head"],
                "sha": self.refs[payload["head"]],
                "repo": self.metadata.copy(),
            },
            "base": {
                "ref": payload["base"],
                "sha": self.refs[payload["base"]],
                "repo": self.metadata.copy(),
            },
        }
        self.records.append(result)
        return copy.deepcopy(result)

    def checks(self, _repository, sha):
        return {"head_sha": sha, "state": self.check_state, "checks": []}


@pytest.fixture
def publication(repository, tmp_path):
    root = tmp_path / "state"
    bench = Workbench(root)
    recipe = Recipe(image=PYTHON_IMAGE, argv=["true"])
    candidate = bench.intake(repository, PATCH, ["calc.py"], recipe)["candidate"]
    policy = bench.policy(candidate)
    # Issued fixture evidence isolates publication tests from execution/model behavior.
    verification = {
        "candidate": candidate,
        "passed": True,
        "exit_code": 0,
        "reason": None,
        "recipe_sha256": digest(canonical(policy["recipe"])),
        "output_sha256": bench.artifacts.put(b"fixture checks passed"),
    }
    inspected = bench.inspect(candidate)
    review = {
        "candidate": candidate,
        "verdict": {
            "schema_version": "1.0",
            "input_sha256": digest(open(inspected["patch"], "rb").read()),
            "risk": "low",
            "blocked": False,
            "findings": [],
            "rationale": "Fixture verdict",
        },
    }
    for name, value in (("verification", verification), ("review", review)):
        atomic_write(bench.root / candidate / f"{name}.json", canonical(value))
    remote = MemoryGitHub(repository)
    publisher = Publisher(root, remote)
    prepared = publisher.prepare(
        candidate,
        "owner/repo",
        branch="development/retry",
        title="Fix value",
        body="Correct the requested value and pass the existing checks.",
    )
    return publisher, remote, prepared["plan_sha256"]


def test_publication_exact_tree_checks_and_idempotency(publication):
    publisher, remote, sha = publication
    assert remote.writes == []
    plan = publisher.load(sha)
    result = publisher.publish(sha)
    assert result["state"] == "published"
    assert result["pull_request"]["draft"]
    assert remote.refs["main"] == plan.base_sha
    assert remote.refs[plan.branch] == plan.head_sha
    assert result["checks"] == {"head_sha": plan.head_sha, "state": "pending", "checks": []}
    writes = remote.writes.copy()
    remote.check_state = "passed"
    assert publisher.publish(sha)["checks"]["state"] == "passed"
    assert remote.writes == writes
    assert remote.writes.count("pull") == remote.writes.count("branch") == 1


@pytest.mark.parametrize("boundary", ["before_branch", "after_branch", "after_pull"])
def test_recovery_after_crash_uses_remote_identity(publication, boundary):
    publisher, remote, sha = publication

    def crash(point):
        if point == boundary:
            raise RuntimeError("simulated lost process")

    publisher.fault = crash
    with pytest.raises(RuntimeError, match="simulated"):
        publisher.publish(sha)
    publisher.fault = lambda _: None
    assert publisher.publish(sha)["state"] == "published"
    assert remote.writes.count("pull") == remote.writes.count("branch") == 1
    assert any(e["event"] == "publication.interrupted" for e in publisher.inspect(sha)["events"])


def test_uncertain_pull_absence_never_reposts_then_reconciles(publication):
    publisher, remote, sha = publication

    def lose_reply(point):
        if point == "after_pull":
            raise RemoteError()

    publisher.fault = lose_reply
    with pytest.raises(RemoteError):
        publisher.publish(sha)
    remote.hidden = True
    writes = remote.writes.copy()
    assert publisher.publish(sha)["state"] == "ambiguous"
    assert publisher.publish(sha, reconcile_only=True)["state"] == "ambiguous"
    assert remote.writes == writes
    remote.hidden = False
    assert publisher.publish(sha, reconcile_only=True)["state"] == "published"
    assert remote.writes == writes


def test_crash_before_pull_does_not_assume_the_request_was_never_sent(publication):
    publisher, remote, sha = publication
    publisher.fault = lambda point: (
        (_ for _ in ()).throw(RuntimeError()) if point == "before_pull" else None
    )
    with pytest.raises(RuntimeError):
        publisher.publish(sha)
    assert publisher.publish(sha)["state"] == "ambiguous"
    assert "pull" not in remote.writes


@pytest.mark.parametrize("change", ["base", "branch", "actor", "repository", "evidence", "plan"])
def test_changed_authority_or_content_blocks_publication(publication, change):
    publisher, remote, sha = publication
    plan = publisher.load(sha)
    if change == "base":
        remote.refs["main"] = "f" * 40
    elif change == "branch":
        remote.refs[plan.branch] = "f" * 40
    elif change == "actor":
        remote.actor["id"] += 1
    elif change == "repository":
        remote.metadata["id"] += 1
    elif change == "plan":
        path = publisher.directory(sha) / "plan.json"
        value = json.loads(path.read_bytes())
        value["body"] = "Substituted content"
        atomic_write(path, canonical(value))
    else:
        path = publisher.bench.root / plan.candidate_sha256 / "review.json"
        value = json.loads(path.read_bytes())
        value["verdict"]["rationale"] = "Substituted review"
        atomic_write(path, canonical(value))
    with pytest.raises(ValueError):
        publisher.publish(sha)
    assert remote.writes == []


def test_reconciliation_is_read_only_and_expiry_cannot_authorize_new_writes(
    publication, monkeypatch
):
    publisher, remote, sha = publication
    assert publisher.publish(sha, reconcile_only=True)["state"] == "prepared"
    assert remote.writes == []
    monkeypatch.setattr(
        "swe_platform.github_publication.time.time", lambda: publisher.load(sha).expires_at + 1
    )
    with pytest.raises(ValueError, match="expired"):
        publisher.publish(sha)
    assert remote.writes == []


def test_closed_or_changed_pull_is_not_reopened_or_overwritten(publication):
    publisher, remote, sha = publication
    publisher.publish(sha)
    remote.records[0]["head"]["sha"] = "f" * 40
    writes = remote.writes.copy()
    with pytest.raises(ValueError, match="identity or revisions"):
        publisher.publish(sha, reconcile_only=True)
    assert remote.writes == writes


def test_tree_identity_matches_git_for_nested_binary_and_executable_files(tmp_path):
    files = {
        "a.bin": {"data": base64.b64encode(b"\x00\xff\n").decode(), "mode": 0o644},
        "a/run": {"data": base64.b64encode(b"#!/bin/sh\nexit 0\n").decode(), "mode": 0o755},
        "a0.txt": {"data": "", "mode": 0o644},
    }
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for path, item in files.items():
        file = tmp_path / path
        file.parent.mkdir(exist_ok=True)
        file.write_bytes(base64.b64decode(item["data"]))
        file.chmod(item["mode"])
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    expected = subprocess.check_output(["git", "write-tree"], cwd=tmp_path).decode().strip()
    assert git_tree(files)[0] == expected


def test_transport_bounds_pins_host_and_hides_raw_error():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return (
            1,
            b'HTTP/2.0 403 Forbidden\r\nContent-Type: application/json\r\n\r\n{"message":"PRIVATE-CANARY"}',
            b"PRIVATE-CANARY",
        )

    github = GitHub(run)
    with pytest.raises(RemoteError) as error:
        github.request("GET", "/user")
    assert "PRIVATE-CANARY" not in str(error.value)
    assert calls[0][0][2:4] == ["--hostname", "github.com"]
    assert calls[0][1]["timeout"] == 30
    assert calls[0][1]["limit"] == 8 * 1024 * 1024


def test_paginated_pull_reconciliation_does_not_stop_at_first_page():
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv[-1])
        data = (
            [{"number": n} for n in range(100)]
            if argv[-1].endswith("&page=1")
            else [{"number": 100}]
        )
        return 0, b"HTTP/2.0 200 OK\n\n" + canonical(data), b""

    result = GitHub(run).pulls("owner/repo", "development/retry")
    assert len(result) == 101 and len(calls) == 2
