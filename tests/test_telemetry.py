import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from swe_platform.io import canonical, digest
from swe_platform.telemetry import ModelLedger, reported_usage, response_values


def ledger(root, *, tokens=50000, requests=2):
    return ModelLedger(root, max_requests=requests, max_tokens=tokens, deadline=time.time() + 60)


BODY = {"messages": [{"role": "user", "content": "private task canary"}], "max_tokens": 4096}


def test_concurrent_requests_share_durable_admission(tmp_path):
    account = ledger(tmp_path, requests=1)

    def reserve(_):
        try:
            return account.reserve("analysis", "specialist", BODY)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sum(value is not None for value in results) == 1
    assert account.summary()["model_requests"] == 1
    assert account.summary()["unresolved_calls"] == 1
    assert "private task canary" not in account.path.read_text()
    resumed = ModelLedger(tmp_path, **account.limits)
    with pytest.raises(ValueError, match="request budget"):
        resumed.reserve("coder", "coder", BODY)


def test_unknown_usage_retains_allowance_and_known_usage_releases_it(tmp_path):
    account = ledger(tmp_path)
    first = account.reserve("coder-0", "coder", BODY)
    account.complete(first, status=200, raw=b'{"choices":[]}', content_type="application/json")
    reserved = account.summary()["tokens_used_or_reserved"]
    second = account.reserve("reviewer-0", "reviewer", BODY)
    data = b'data: {"usage":{"prompt_tokens":17,"completion_tokens":3,"total_tokens":20}}\n\ndata: [DONE]\n\n'
    account.complete(second, status=200, raw=data, content_type="text/event-stream")
    summary = account.summary()
    assert summary["tokens_used_or_reserved"] == reserved + 20
    assert summary["reported_tokens"] == 20 and summary["total_tokens"] is None
    with pytest.raises(ValueError, match="already has a receipt"):
        account.complete(second, status=200, raw=b"{}")


def test_request_and_usage_overruns_are_stopped(tmp_path):
    account = ledger(tmp_path, tokens=15000)
    call = account.reserve("coder-0", "coder", BODY)
    with pytest.raises(ValueError, match="admission budget"):
        account.reserve("coder-0", "coder", BODY)
    with pytest.raises(ValueError, match="exceeded admission"):
        account.complete(
            call,
            status=200,
            raw=canonical(
                {"usage": {"prompt_tokens": 20000, "completion_tokens": 2, "total_tokens": 20002}}
            ),
        )
    assert account.summary()["reported_tokens"] == 20002
    with pytest.raises(ValueError, match="admission budget"):
        account.reserve("coder-0", "coder", BODY)


def test_tool_trace_records_digests_without_content(tmp_path):
    account = ledger(tmp_path)
    body = {
        **BODY,
        "messages": [
            {"role": "tool", "tool_call_id": "call-1", "content": "sensitive tool output"}
        ],
    }
    call = account.reserve("coder", "coder", body)
    account.complete(
        call,
        status=200,
        raw=canonical(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-2",
                                    "function": {"name": "read", "arguments": "private/file.py"},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
    )
    saved = account.read()["calls"][0]
    assert saved["tool_observations"][0]["content_sha256"] == digest(
        canonical("sensitive tool output")
    )
    assert saved["tool_calls"][0]["name"] == "read"
    assert (
        "sensitive" not in account.path.read_text()
        and "private/file.py" not in account.path.read_text()
    )


def test_malformed_usage_is_unknown_and_nan_has_no_canonical_identity():
    assert (
        reported_usage([{"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 2}}])
        is None
    )
    assert (
        reported_usage(
            [{"usage": {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3}}]
        )
        is None
    )
    assert response_values(b'data:{"choices":[]}\n\ndata: [DONE]\n', "text/event-stream") == [
        {"choices": []}
    ]
    with pytest.raises(ValueError):
        canonical({"score": float("nan")})
