"""Durable model accounting and content-free execution traces.

The broker observes usage and tool exchanges. A saved intent is charged until an
actual usage receipt resolves it. No prompt, tool output or private profile is exported.
"""

import json
import time
import uuid
from pathlib import Path

from .io import atomic_write, canonical, digest, lock


def response_values(raw, content_type):
    if "text/event-stream" in content_type:
        return [
            json.loads(line[5:].strip())
            for line in raw.decode().splitlines()
            if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]")
        ]
    return [json.loads(raw)]


def reported_usage(values):
    found = [value["usage"] for value in values if isinstance(value.get("usage"), dict)]
    if not found:
        return None
    usage = found[-1]
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    if any(type(usage.get(key)) is not int or usage[key] < 0 for key in fields):
        return None
    if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
        return None
    return {key: usage[key] for key in fields}


class ModelLedger:
    def __init__(self, directory: Path, *, max_requests, max_tokens, deadline):
        self.directory = directory
        self.path = directory / "model-ledger.json"
        self.limits = {"max_requests": max_requests, "max_tokens": max_tokens, "deadline": deadline}
        with lock(directory / "model-ledger.lock", timeout=15):
            if self.path.exists():
                if self.read()["limits"] != self.limits:
                    raise ValueError("Model ledger already has another budget")
            else:
                atomic_write(
                    self.path,
                    canonical(
                        {"schema_version": "model-ledger/v1", "limits": self.limits, "calls": []}
                    ),
                )

    def read(self):
        return json.loads(self.path.read_bytes())

    def summary(self):
        record = self.read()
        calls = record["calls"]
        known = [call["usage"]["total_tokens"] for call in calls if call.get("usage") is not None]
        return {
            "max_tokens": record["limits"]["max_tokens"],
            "model_requests": len(calls),
            "reported_tokens": sum(known),
            "total_tokens": sum(known) if len(known) == len(calls) else None,
            "tokens_used_or_reserved": sum(call["charged_tokens"] for call in calls),
            "unresolved_calls": sum(call.get("usage") is None for call in calls),
            "cost_usd": None,
            "provenance": "producer_observed_gateway_usage",
        }

    def stage(self, gateway, stage, role):
        return MeteredGateway(self, gateway, stage, role)

    def reserve(self, stage, role, body):
        # Byte-based admission is deliberately conservative, not a claimed tokenizer.
        # Reserve the serialized input plus protocol margin and full output allowance.
        allowance = (
            len(canonical(body)) + 8192 + 128 * len(body.get("messages", [])) + body["max_tokens"]
        )
        with lock(self.directory / "model-ledger.lock", timeout=15):
            record = self.read()
            if time.time() >= record["limits"]["deadline"] or (self.directory / "cancel").exists():
                raise ValueError("Model dispatch is no longer admitted")
            if len(record["calls"]) >= record["limits"]["max_requests"]:
                raise ValueError("Shared model request budget exhausted")
            if (
                sum(call["charged_tokens"] for call in record["calls"]) + allowance
                > record["limits"]["max_tokens"]
            ):
                raise ValueError("Shared token admission budget exhausted")
            call_id = uuid.uuid4().hex
            tools = []
            for message in body.get("messages", []):
                if message.get("role") == "tool":
                    tools.append(
                        {
                            "call_sha256": digest(str(message.get("tool_call_id", "")).encode()),
                            "content_sha256": digest(canonical(message.get("content"))),
                        }
                    )
            record["calls"].append(
                {
                    "id": call_id,
                    "stage": stage,
                    "role": role,
                    "started_at": time.time(),
                    "state": "dispatched",
                    "reserved_tokens": allowance,
                    "charged_tokens": allowance,
                    "request_sha256": digest(canonical(body)),
                    "tool_observations": tools,
                    "usage": None,
                }
            )
            atomic_write(self.path, canonical(record))
            return call_id

    def complete(self, call_id, *, status=None, content_type="", raw=b"", error=None):
        values = response_values(raw, content_type) if status == 200 else []
        usage = reported_usage(values)
        with lock(self.directory / "model-ledger.lock", timeout=15):
            record = self.read()
            call = next(item for item in record["calls"] if item["id"] == call_id)
            if call["state"] != "dispatched":
                raise ValueError("Model call already has a receipt")
            call.update(
                {
                    "state": "completed" if status == 200 else "unresolved",
                    "finished_at": time.time(),
                    "http_status": status,
                    "error_class": error,
                    "response_sha256": digest(raw),
                    "usage": usage,
                }
            )
            if usage is not None:
                call["charged_tokens"] = usage["total_tokens"]
            tool_calls = {}
            for value in values:
                for choice in value.get("choices", []):
                    message = choice.get("delta", choice.get("message", {}))
                    for tool in message.get("tool_calls", []):
                        key = str((choice.get("index", 0), tool.get("index", tool.get("id", ""))))
                        item = tool_calls.setdefault(key, {"name": "", "arguments": "", "id": ""})
                        item["name"] += tool.get("function", {}).get("name", "")
                        item["arguments"] += tool.get("function", {}).get("arguments", "")
                        item["id"] += tool.get("id", "")
            permitted = {"read", "write", "edit", "bash", "grep", "glob"}
            call["tool_calls"] = [
                {
                    "name": tool["name"] if tool["name"] in permitted else "undeclared",
                    "call_sha256": digest(tool["id"].encode()),
                    "arguments_sha256": digest(tool["arguments"].encode()),
                }
                for tool in tool_calls.values()
            ]
            atomic_write(self.path, canonical(record))
            if (
                sum(item["charged_tokens"] for item in record["calls"])
                > record["limits"]["max_tokens"]
            ):
                raise ValueError(
                    "Reported token use exceeded admission bound; further dispatch blocked"
                )


class MeteredGateway:
    def __init__(self, ledger, gateway, stage, role):
        self.ledger, self.gateway, self.stage_name, self.role = ledger, gateway, stage, role
        self.profile, self.identity_sha256 = gateway.profile, gateway.identity_sha256
        self.environment = getattr(gateway, "environment", {})

    def request(self, body, deadline):
        call_id = self.ledger.reserve(self.stage_name, self.role, body)
        try:
            status, content_type, raw = self.gateway.request(body, deadline)
        except BaseException as exc:
            self.ledger.complete(call_id, error=type(exc).__name__)
            raise
        self.ledger.complete(call_id, status=status, content_type=content_type, raw=raw)
        return status, content_type, raw


def execution_trace(directory: Path):
    record = json.loads((directory / "job.json").read_bytes())
    ledger_path = directory / "model-ledger.json"
    calls = json.loads(ledger_path.read_bytes())["calls"] if ledger_path.exists() else []
    spans = []
    for name, step in record["steps"].items():
        events = [event for event in record["events"] if event["event"].startswith(name + ".")]
        spans.append(
            {
                "span_id": digest((directory.name + name).encode())[:16],
                "parent_span_id": None,
                "kind": "stage",
                "name": name,
                "started_at": events[0]["at"] if events else None,
                "finished_at": events[-1]["at"] if "result" in step and events else None,
                "state": "completed" if "result" in step else "unresolved",
                "candidate_sha256": step.get("result", {}).get("candidate"),
            }
        )
    seen = set()
    for call in calls:
        parent = digest((directory.name + call["stage"]).encode())[:16]
        spans.append(
            {"span_id": call["id"][:16], "parent_span_id": parent, "kind": "model", **call}
        )
        for tool in call.get("tool_observations", []):
            identity = (call["stage"], tool["call_sha256"])
            if identity not in seen:
                seen.add(identity)
                spans.append(
                    {
                        "span_id": digest(canonical(identity))[:16],
                        "parent_span_id": parent,
                        "kind": "tool",
                        "state": "producer_observed_result",
                        **tool,
                    }
                )
    publications = []
    for path in sorted((directory / "evidence/publications").glob("*/journal.json")):
        journal = json.loads(path.read_bytes())
        publications.append(
            {
                "plan_sha256": path.parent.name,
                "state": journal["state"],
                "events": journal["events"],
            }
        )
    return {
        "schema_version": "development-trace/v1",
        "trace_id": directory.name[:32],
        "workflow_id": directory.name,
        "policy_sha256": record["request"].get("policy_sha256"),
        "state": record["state"],
        "events": record["events"],
        "spans": spans,
        "publications": publications,
    }
