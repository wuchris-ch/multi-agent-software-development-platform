"""Local evidence-binding proposal; deliberately distinct from evaluator wire schemas."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .io import Artifacts, canonical, digest
from .models import StrictModel

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Revision = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]


class ArtifactReference(StrictModel):
    role: Literal["patch", "manifest", "public-verification", "review"]
    sha256: Sha256
    storage_key: Sha256

    @model_validator(mode="after")
    def content_addressed(self):
        if self.sha256 != self.storage_key:
            raise ValueError("Only approved content-addressed local keys are supported")
        return self


class Usage(StrictModel):
    total_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Submission(StrictModel):
    schema_version: Literal["swe-platform.evaluation-proposal/v1"] = (
        "swe-platform.evaluation-proposal/v1"
    )
    execution_id: str = Field(min_length=1, max_length=200)
    producer_run_id: str = Field(min_length=1, max_length=200)
    base_revision: Revision
    # Local candidates have no commit until separately authorized. Never invent one.
    candidate_revision: Revision | None = None
    candidate_tree_sha256: Sha256
    recipe_sha256: Sha256
    artifacts: list[ArtifactReference] = Field(min_length=1, max_length=20)
    trace_reference: None = None
    usage: Usage = Field(default_factory=Usage)
    producer_status: Literal["completed"] = "completed"


class Decision(StrictModel):
    schema_version: Literal["swe-platform.decision-binding/v1"] = "swe-platform.decision-binding/v1"
    execution_id: str
    submission_sha256: Sha256
    assessment_set_sha256: Sha256
    policy_sha256: Sha256
    evaluator_revision: Revision
    outcome: Literal["pass", "fail", "inconclusive"]


def validate_submission(submission: Submission, artifacts: Artifacts, issued_execution_id: str):
    if submission.execution_id != issued_execution_id:
        raise ValueError("Submission does not match the issued execution")
    for reference in submission.artifacts:
        artifacts.get(reference.storage_key)
    return digest(canonical(submission.model_dump(mode="json")))


def validate_decision(
    decision: Decision, submission: Submission, *, policy_sha256: str, evaluator_revision: str
):
    if (
        decision.execution_id != submission.execution_id
        or decision.submission_sha256 != digest(canonical(submission.model_dump(mode="json")))
        or decision.policy_sha256 != policy_sha256
        or decision.evaluator_revision != evaluator_revision
    ):
        raise ValueError("Stale or incompatible evaluator decision")
    # This checks content binding only. A real transport must also authenticate origin.
    return decision.outcome
