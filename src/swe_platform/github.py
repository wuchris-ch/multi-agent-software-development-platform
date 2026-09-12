"""Bounded GitHub transport using the operator's existing GitHub CLI login."""

import json
import os
import re
from urllib.parse import quote, urlencode

from .io import canonical
from .process import bounded_run


class RemoteError(RuntimeError):
    def __init__(self, status=None):
        self.status = status
        super().__init__(
            f"GitHub request failed (HTTP {status})"
            if status
            else "GitHub response unavailable; reconcile the saved operation"
        )


class GitHub:
    def __init__(self, run=bounded_run):
        self.run = run

    def request(self, method, path, payload=None, *, missing=False):
        if not path.startswith("/") or path.startswith("//") or ".." in path:
            raise ValueError("Expected a repository-scoped GitHub API path")
        argv = [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--include",
            "--method",
            method,
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
            path,
        ]
        if payload is not None:
            argv += ["--input", "-"]
        try:
            code, output, _ = self.run(
                argv,
                payload=canonical(payload) if payload is not None else b"",
                env={**os.environ, "GH_PROMPT_DISABLED": "1", "GH_DEBUG": "", "GH_PAGER": "cat"},
                timeout=30,
                limit=8 * 1024 * 1024,
            )
        except (OSError, TimeoutError, ValueError):
            raise RemoteError() from None
        header, separator, body = output.replace(b"\r\n", b"\n").partition(b"\n\n")
        match = re.match(rb"HTTP/\S+ (\d{3})", header)
        status = int(match[1]) if match else None
        if status == 404 and missing:
            return None
        if code or not separator or status is None or not 200 <= status < 300:
            raise RemoteError(status)
        try:
            return json.loads(body)
        except (ValueError, UnicodeError):
            raise RemoteError(status) from None

    def identity(self):
        return self.request("GET", "/user")

    def repository(self, repository):
        return self.request("GET", f"/repos/{repository}")

    def branch(self, repository, branch):
        result = self.request(
            "GET", f"/repos/{repository}/git/ref/heads/{quote(branch, safe='')}", missing=True
        )
        if result is None:
            return None
        if result.get("ref") != "refs/heads/" + branch or result["object"]["type"] != "commit":
            raise ValueError("GitHub returned a different branch")
        return result["object"]["sha"]

    def object(self, repository, kind, sha):
        return self.request("GET", f"/repos/{repository}/git/{kind}/{sha}", missing=True)

    def create_object(self, repository, kind, payload):
        return self.request("POST", f"/repos/{repository}/git/{kind}", payload)

    def tree(self, repository, sha):
        result = self.request("GET", f"/repos/{repository}/git/trees/{sha}?recursive=1")
        if result.get("truncated"):
            raise ValueError("GitHub tree is truncated; exact verification requires all entries")
        return {
            entry["path"]: {"sha": entry["sha"], "mode": entry["mode"]}
            for entry in result["tree"]
            if entry["type"] != "tree"
        }

    def create_branch(self, repository, branch, sha):
        return self.request(
            "POST", f"/repos/{repository}/git/refs", {"ref": "refs/heads/" + branch, "sha": sha}
        )

    def pages(self, path, *, field=None):
        separator = "&" if "?" in path else "?"
        for page in range(1, 101):
            result = self.request("GET", f"{path}{separator}per_page=100&page={page}")
            entries = result[field] if field else result
            if not isinstance(entries, list):
                raise ValueError("Invalid GitHub page")
            yield from entries
            if len(entries) < 100:
                return
        raise ValueError("GitHub pagination limit reached; reconciliation is incomplete")

    def pulls(self, repository, branch):
        query = urlencode({"state": "all", "head": repository.split("/")[0] + ":" + branch})
        return list(self.pages(f"/repos/{repository}/pulls?{query}"))

    def pull(self, repository, number):
        return self.request("GET", f"/repos/{repository}/pulls/{number}")

    def create_pull(self, repository, payload):
        return self.request("POST", f"/repos/{repository}/pulls", payload)

    def checks(self, repository, sha):
        runs = list(
            self.pages(
                f"/repos/{repository}/commits/{sha}/check-runs?filter=latest", field="check_runs"
            )
        )
        statuses = list(self.pages(f"/repos/{repository}/commits/{sha}/statuses"))
        contexts = {}
        for status in statuses:  # GitHub returns newest first for each context.
            contexts.setdefault(status["context"], status)
        checks = [
            {
                "name": run["name"],
                "state": run["conclusion"] if run["status"] == "completed" else "pending",
                "url": run.get("html_url"),
            }
            for run in runs
        ]
        checks += [
            {"name": s["context"], "state": s["state"], "url": s.get("target_url")}
            for s in contexts.values()
        ]
        states = {check["state"] for check in checks}
        state = (
            "failed"
            if states
            & {
                "failure",
                "error",
                "timed_out",
                "cancelled",
                "action_required",
                "startup_failure",
                "stale",
            }
            else (
                "pending"
                if "pending" in states
                else "passed"
                if states and "success" in states and states <= {"success", "neutral", "skipped"}
                else "no_checks"
                if not states
                else "unresolved"
            )
        )
        return {"head_sha": sha, "state": state, "checks": checks}
