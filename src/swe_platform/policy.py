"""Versioned production policy and validated, read-only planning handoffs."""

from typing import Literal

from pydantic import Field, model_validator

from .io import canonical, digest
from .models import StrictModel
from .sandbox.docker import safe_path


class DevelopmentPolicy(StrictModel):
    schema_version: Literal["development-policy/v1"] = "development-policy/v1"
    name: str = Field(default="review", pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    mode: Literal["single", "review", "selective"] = "review"
    coder_requests: int = Field(default=12, ge=2, le=30)
    reviewer_requests: int = Field(default=2, ge=1, le=3)
    planner_requests: int = Field(default=3, ge=1, le=5)
    specialist_requests: int = Field(default=2, ge=1, le=3)
    max_specialists: int = Field(default=2, ge=0, le=2)
    coding_guidance: str = Field(default="", max_length=2000)

    @property
    def sha256(self):
        return digest(canonical(self.model_dump()))

    @classmethod
    def preset(cls, mode):
        return cls(name=mode, mode=mode)


class PlanStep(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    goal: str = Field(min_length=1, max_length=1000)
    paths: list[str] = Field(min_length=1, max_length=30)


class SpecialistRequest(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    focus: str = Field(min_length=1, max_length=1000)
    paths: list[str] = Field(min_length=1, max_length=30)


class DevelopmentPlan(StrictModel):
    schema_version: Literal["development-plan/v1"] = "development-plan/v1"
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    summary: str = Field(min_length=1, max_length=2000)
    steps: list[PlanStep] = Field(min_length=1, max_length=12)
    specialists: list[SpecialistRequest] = Field(default_factory=list, max_length=2)

    def check(self, files, allowed, limit):
        if len(canonical(self.model_dump())) > 6000:
            raise ValueError("Plan exceeds the handoff size limit")
        if self.snapshot_sha256 != digest(canonical(files)):
            raise ValueError("Plan belongs to another source snapshot")
        if len(self.specialists) > limit:
            raise ValueError("Plan exceeds the specialist limit")
        ids, read_paths = set(), set()
        for step in self.steps:
            if step.id in ids or set(step.paths) - set(allowed):
                raise ValueError("Plan has conflicting steps or exceeds the write scope")
            ids.add(step.id)
        ids.clear()
        for request in self.specialists:
            if request.id in ids or set(request.paths) - set(files):
                raise ValueError("Specialist identity or input scope is invalid")
            if read_paths.intersection(request.paths):
                raise ValueError("Specialist inputs must describe separate work")
            ids.add(request.id)
            read_paths.update(request.paths)
        for item in [*self.steps, *self.specialists]:
            for path in item.paths:
                safe_path(path)
        return self


class AnalysisHandoff(StrictModel):
    schema_version: Literal["development-analysis/v1"] = "development-analysis/v1"
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    specialist_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    paths: list[str] = Field(min_length=1, max_length=30)
    findings: list[str] = Field(max_length=12)
    recommendation: str = Field(min_length=1, max_length=3000)

    @model_validator(mode="after")
    def bounded(self):
        if any(len(value) > 1000 for value in self.findings):
            raise ValueError("Analysis finding is too long")
        return self

    def check(self, request, files):
        if (
            self.snapshot_sha256 != digest(canonical(files))
            or self.specialist_id != request.id
            or set(self.paths) != set(request.paths)
        ):
            raise ValueError("Analysis does not match its immutable handoff")
        return self
