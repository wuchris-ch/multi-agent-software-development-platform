"""Reviewable publication plans and fake-transport reconciliation only.

There is deliberately no live push or GitHub client in this module.
"""

import time
from typing import Literal

from pydantic import Field

from .io import canonical, digest
from .models import StrictModel


class PublicationPlan(StrictModel):
    schema_version: Literal["publication-plan/v1"] = "publication-plan/v1"
    job_id: str
    repository: str = Field(pattern=r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
    branch: str = Field(pattern=r"^agent/job-[a-zA-Z0-9-]+$")
    base_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    head_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    candidate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(max_length=16000)

    @property
    def marker(self):
        return f"<!-- swe-platform job:{self.job_id} candidate:{self.candidate_sha256} -->"


class SimulationAuthorization(StrictModel):
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expires_at: float
    simulation_only: Literal[True] = True


def simulate(plan: PublicationPlan, authorization: SimulationAuthorization, remote, ledger: dict):
    """Caller persists the ledger between invocations. Tests exercise an offline remote."""
    if type(remote) is not FakeGitHub:
        raise ValueError("Only the offline fake transport is enabled")
    if authorization.expires_at <= time.time() or authorization.plan_sha256 != digest(
        canonical(plan.model_dump())
    ):
        raise ValueError("Authorization is expired or belongs to another exact plan")
    if remote.base_sha != plan.base_sha or remote.head_sha != plan.head_sha:
        return {"state": "stale"}
    matches = []
    for page in remote.pages():
        for pr in page:
            if (
                pr["repository"],
                pr["branch"],
                pr["base_sha"],
                pr["head_sha"],
                pr["author"],
                pr["marker"],
            ) == (
                plan.repository,
                plan.branch,
                plan.base_sha,
                plan.head_sha,
                remote.author,
                plan.marker,
            ):
                matches.append(pr)
    if len(matches) > 1:
        return {"state": "ambiguous"}
    if matches:
        ledger.update(state="confirmed", remote_id=matches[0]["id"])
        return dict(ledger)
    if ledger.get("state") in ("in_flight", "ambiguous"):
        return {"state": "ambiguous"}
    ledger["state"] = "in_flight"
    remote.create(plan)
    if remote.lose_response:
        return {"state": "ambiguous"}
    return simulate(plan, authorization, remote, ledger)


class FakeGitHub:
    def __init__(self, base_sha, head_sha, *, lose_response=False):
        self.base_sha, self.head_sha = base_sha, head_sha
        self.author = "offline-fixture"
        self.lose_response = lose_response
        self.records = []
        self.posts = 0

    def pages(self):
        for index in range(0, len(self.records), 2):
            yield self.records[index : index + 2]

    def create(self, plan):
        self.posts += 1
        self.records.append(
            {
                "id": str(self.posts),
                "repository": plan.repository,
                "branch": plan.branch,
                "base_sha": plan.base_sha,
                "head_sha": plan.head_sha,
                "author": self.author,
                "marker": plan.marker,
                "draft": True,
            }
        )
