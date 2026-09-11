import json

TESTED_VERSION = "0.153.4"


def capabilities():
    return {
        "adapter": "codex-jsonl/v1",
        "tested_cli_version": TESTED_VERSION,
        "enabled": True,
        "transport": "host broker over attached container pipes; worker network disabled",
        "usage": "tokens_if_reported; cost_unknown",
    }


def start(root, *args, **kwargs):
    from ..broker.host import CodingRun

    return CodingRun(root).run(*args, **kwargs)


def invocation(model: str):
    """Candidate argv for a future external sandbox, not permission to execute it."""
    if not model or len(model) > 100 or "\0" in model:
        raise ValueError("Invalid model identifier")
    return [
        "codex",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--json",
        "--sandbox",
        "workspace-write",
        "--model",
        model,
        "-",
    ]


def collect(raw: bytes, exit_code: int):
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Codex stream too large")
    if exit_code != 0 or not raw.endswith(b"\n"):
        raise ValueError("Incomplete Codex execution")
    session = None
    completed = None
    message = None
    for line in raw.splitlines():
        value = json.loads(line)
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise ValueError("Malformed Codex event")
        kind = value["type"]
        if completed is not None:
            raise ValueError("Events appeared after terminal completion")
        if kind in ("error", "turn.failed"):
            raise ValueError("Codex reported execution failure")
        if kind == "thread.started":
            session = value.get("thread_id")
        elif kind == "item.completed":
            item = value.get("item", {})
            if item.get("type") == "agent_message":
                message = item.get("text")
        elif kind == "turn.completed":
            completed = value
    if completed is None or not isinstance(message, str):
        raise ValueError("Codex has no final message and completed turn")
    usage = completed.get("usage")
    if usage is not None:
        if not isinstance(usage, dict) or any(type(v) is not int or v < 0 for v in usage.values()):
            raise ValueError("Invalid Codex usage")
    return {
        "session_id": session,
        "message": message,
        "usage": usage,
        "cost_usd": None,
        "usage_complete": usage is not None,
    }
