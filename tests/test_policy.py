import base64
import json
import time

import pytest

from swe_platform.broker.host import BrokerPolicy, validate_reply_tools
from swe_platform.io import canonical, digest
from swe_platform.policy import AnalysisHandoff, DevelopmentPlan, DevelopmentPolicy


def test_analysis_role_rejects_write_and_shell_at_both_broker_boundaries():
    for role in ("planner", "specialist"):
        for tool in ("write", "edit", "bash", "task"):
            body = {"tools": [{"type": "function", "function": {"name": tool}}]}
            frame = {
                "type": "model_request",
                "id": "test",
                "path": "/v1/chat/completions",
                "body": base64.b64encode(canonical(body)).decode(),
            }
            with pytest.raises(ValueError, match="not permitted"):
                BrokerPolicy("fixture", role=role, deadline=time.time() + 60).authorize(frame)
            reply = canonical(
                {"choices": [{"message": {"tool_calls": [{"function": {"name": tool}}]}}]}
            )
            with pytest.raises(ValueError, match="outside the agent role"):
                validate_reply_tools(reply, "application/json", role)


def test_plan_and_specialists_bind_source_and_scope():
    files = {"a.py": {"data": "YQ==", "mode": 420}, "b.py": {"data": "Yg==", "mode": 420}}
    plan = DevelopmentPlan(
        snapshot_sha256=digest(canonical(files)),
        summary="Update both layers",
        steps=[{"id": "implement", "goal": "Change both", "paths": ["a.py", "b.py"]}],
        specialists=[
            {"id": "first", "focus": "Inspect a", "paths": ["a.py"]},
            {"id": "second", "focus": "Inspect b", "paths": ["b.py"]},
        ],
    )
    plan.check(files, list(files), 2)
    with pytest.raises(ValueError, match="specialist limit"):
        plan.check(files, list(files), 1)
    with pytest.raises(ValueError, match="another source"):
        plan.check({"a.py": files["a.py"]}, list(files), 2)
    invalid = plan.model_copy(deep=True)
    invalid.specialists[1].paths = ["a.py"]
    with pytest.raises(ValueError, match="separate work"):
        invalid.check(files, list(files), 2)
    task = plan.specialists[0]
    subset = {"a.py": files["a.py"]}
    handoff = AnalysisHandoff(
        snapshot_sha256=digest(canonical(subset)),
        specialist_id="first",
        paths=["a.py"],
        findings=["Function is local"],
        recommendation="Preserve its caller contract",
    )
    handoff.check(task, subset)
    with pytest.raises(ValueError, match="immutable handoff"):
        handoff.check(plan.specialists[1], subset)
    assert DevelopmentPolicy.preset("single").sha256 != DevelopmentPolicy.preset("review").sha256
    assert (
        DevelopmentPolicy.model_validate_json(json.dumps(DevelopmentPolicy().model_dump())).sha256
        == DevelopmentPolicy().sha256
    )
