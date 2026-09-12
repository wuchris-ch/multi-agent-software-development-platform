import json


def capabilities():
    return {
        "adapter": "flue-result/v1",
        "runtime_version": "2.0.3",
        "roles": ["coder", "reviewer", "planner", "specialist"],
        "transport": "model gateway through a bounded host broker",
        "usage": "tokens_if_reported; cost_unknown",
    }


def collect(raw: bytes, exit_code: int, *, role="coder"):
    if len(raw) > 2 * 1024 * 1024 or exit_code != 0 or not raw.endswith(b"\n"):
        raise ValueError("Incomplete Flue execution")
    result = json.loads(raw)
    if (
        not isinstance(result, dict)
        or set(result) != {"schema_version", "role", "submission_id", "message", "usage", "status"}
        or result["schema_version"] != "flue-result/v1"
        or result["status"] != "completed"
        or result["role"] != role
        or not isinstance(result["submission_id"], str)
        or not result["submission_id"]
        or not isinstance(result["message"], str)
        or not result["message"].strip()
    ):
        raise ValueError("Invalid Flue completion")
    usage = result["usage"]
    normalized = None
    if usage is not None:
        fields = {
            "input": "input_tokens",
            "output": "output_tokens",
            "cacheRead": "cached_input_tokens",
            "cacheWrite": "cache_write_input_tokens",
            "totalTokens": "total_tokens",
        }
        if not isinstance(usage, dict) or any(
            type(usage.get(k)) is not int or usage[k] < 0 for k in fields
        ):
            raise ValueError("Invalid Flue usage")
        normalized = {v: usage[k] for k, v in fields.items()}
    return {
        "message": result["message"],
        "submission_id": result["submission_id"],
        "usage": normalized,
        "cost_usd": None,
    }
