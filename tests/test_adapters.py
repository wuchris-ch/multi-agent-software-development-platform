import json
import sys

import pytest

from swe_platform.adapters import codex
from swe_platform.io import digest
from swe_platform.process import bounded_run
from swe_platform.review.flue import validate
from swe_platform.review.repair import RepairPolicy

DIFF = b"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"


def verdict(**updates):
    result = {
        "schema_version": "1.0",
        "input_sha256": digest(DIFF),
        "risk": "low",
        "blocked": False,
        "findings": [],
        "rationale": "No findings.",
    }
    result.update(updates)
    return json.dumps(result).encode()


def test_blocked_zero_exit_still_blocks():
    raw = verdict(
        risk="medium",
        blocked=True,
        findings=[
            {
                "severity": "major",
                "category": "correctness",
                "file": "a.py",
                "line": 1,
                "detail": "Broken output",
            }
        ],
    )
    code, output, _ = bounded_run(
        [sys.executable, "-c", "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())"],
        payload=raw,
    )
    assert code == 0
    assert validate(output, DIFF).blocked


@pytest.mark.parametrize(
    "updates",
    [
        {"input_sha256": "0" * 64},
        {"blocked": True},
        {"risk": "high"},
        {"extra": "value"},
        {
            "findings": [
                {
                    "severity": "minor",
                    "category": "style",
                    "file": "a.py",
                    "line": 8,
                    "detail": "outside",
                }
            ]
        },
        {
            "findings": [
                {
                    "severity": "minor",
                    "category": "style",
                    "file": "../a.py",
                    "line": 1,
                    "detail": "escape",
                }
            ]
        },
    ],
)
def test_invalid_review_fails_closed(updates):
    with pytest.raises(ValueError):
        validate(verdict(**updates), DIFF)


def test_oversized_and_truncated_review():
    with pytest.raises(ValueError):
        validate(b"x" * (512 * 1024 + 1), DIFF)
    with pytest.raises(ValueError):
        validate(verdict()[:-1], DIFF)


def test_repair_limits_and_identical_candidate():
    policy = RepairPolicy()
    assert policy.decide("one", False, True, "finding1")[0] == "repairing"
    assert policy.decide("two", False, True, "finding2")[0] == "repairing"
    assert policy.decide("three", False, True, "finding3")[0] == "needs_attention"
    assert (
        RepairPolicy(candidates=["same"]).decide("same", True, False, "none")[0]
        == "needs_attention"
    )
    assert RepairPolicy().decide("ok", True, False, "none")[0] == "ready_local"


def test_codex_stream_and_explicit_broker_path():
    events = [
        {"type": "thread.started", "thread_id": "session"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 4}},
    ]
    raw = b"".join(json.dumps(e).encode() + b"\n" for e in events)
    assert codex.collect(raw, 0)["cost_usd"] is None
    with pytest.raises(ValueError):
        codex.collect(raw[:-1], 0)
    with pytest.raises(ValueError):
        codex.collect(raw, 1)
    assert codex.capabilities()["enabled"]
    assert "--ignore-user-config" in codex.invocation("explicit-model")


def test_bounded_adapter_process():
    with pytest.raises(ValueError, match="output limit"):
        bounded_run([sys.executable, "-c", "while True: print('x'*8192)"], limit=1000)
    with pytest.raises(TimeoutError):
        bounded_run([sys.executable, "-c", "import time;time.sleep(10)"], timeout=0.1)
