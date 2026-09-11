from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, validate_default=True)


class State(StrEnum):
    queued = "queued"
    implementing = "implementing"
    verifying = "verifying"
    reviewing = "reviewing"
    repairing = "repairing"
    ready_local = "ready_local"
    cancelling = "cancelling"
    cancelled = "cancelled"
    needs_attention = "needs_attention"
    failed_infra = "failed_infra"
    rejected = "rejected"
    stale = "stale"


TERMINAL = {
    State.ready_local,
    State.cancelled,
    State.needs_attention,
    State.failed_infra,
    State.rejected,
    State.stale,
}
EDGES = {
    State.queued: {State.implementing},
    State.implementing: {State.verifying},
    State.verifying: {State.ready_local, State.reviewing, State.repairing, State.rejected},
    State.reviewing: {State.ready_local, State.repairing, State.rejected},
    State.repairing: {State.implementing},
    State.ready_local: {State.stale},
    State.cancelling: {State.cancelled, State.needs_attention},
}


class Submission(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    key: str = Field(min_length=1, max_length=200)
    task: str = Field(default="Fix add(2, 3) to return 5", max_length=16000)
    adapter: Literal["scripted", "docker-scripted"] = "scripted"
    delay_seconds: float = Field(default=0.1, ge=0, le=300)
    deadline_seconds: float = Field(default=60, ge=1, le=1200)
    behavior: Literal["fix", "wrong", "ignore_cancel"] = "fix"


class Completion(StrictModel):
    attempt_id: str
    epoch: int
    outcome: Literal["completed", "cancelled", "timeout", "error"]


class Conflict(ValueError):
    pass


class Fenced(Conflict):
    pass
