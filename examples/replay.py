"""Run Flue, verification, process recovery and repair with deterministic responses."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from swe_platform.candidates import Recipe, Workbench
from swe_platform.evidence import write_bundle
from swe_platform.io import atomic_write, canonical, digest
from swe_platform.sandbox.docker import PYTHON_IMAGE
from swe_platform.workflow import Workflow
from swe_platform.workspace.snapshot import git

KEY = "replay-public-check-repair"


class ReplayGateway:
    """A fixed transcript transport. No endpoint, credentials or network requests."""

    profile = SimpleNamespace(model="replay-fixture")
    identity_sha256 = digest(b"development-replay-fixture/v1")

    def request(self, body, deadline):
        messages = json.dumps(body["messages"])
        tool = None
        if body.get("tools"):
            if not any(message["role"] == "tool" for message in body["messages"]):
                value = 3 if "Public checks failed on the previous attempt" in messages else 2
                tool = {
                    "index": 0,
                    "id": "replay-write",
                    "type": "function",
                    "function": {
                        "name": "write",
                        "arguments": json.dumps(
                            {"path": "value.py", "content": f"value = {value}\n"}
                        ),
                    },
                }
            answer = "Updated the requested value."
        else:
            answer = json.dumps(
                {
                    "schema_version": "1.0",
                    "input_sha256": re.search(r"input_sha256: ([a-f0-9]{64})", messages)[1],
                    "risk": "low",
                    "blocked": False,
                    "findings": [],
                    "rationale": "Replay fixture review of the exact patch.",
                }
            )
        chunk = {
            "id": "replay-completion",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "worker",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "tool_calls": [tool]}
                    if tool
                    else {"role": "assistant", "content": answer},
                    "finish_reason": None,
                }
            ],
        }
        final = {
            **chunk,
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool else "stop"}
            ],
        }
        raw = b"".join(b"data: " + canonical(event) + b"\n\n" for event in (chunk, final))
        return 200, "text/event-stream", raw + b"data: [DONE]\n\n"


def initialize(root):
    marker = root / "replay.json"
    if root.exists():
        if not marker.exists() or json.loads(marker.read_bytes()) != {"replay": 1, "key": KEY}:
            raise ValueError("Choose a new replay directory")
        return
    root.mkdir(parents=True, mode=0o700)
    source = root / "source"
    source.mkdir()
    git(source, "init", "-q", "--template=")
    atomic_write(source / "value.py", b"value = 1\n")
    atomic_write(
        source / "test_value.py",
        b"import unittest\nfrom value import value\n\n"
        b"class ValueTest(unittest.TestCase):\n"
        b"    def test_requested_value(self):\n        self.assertEqual(value, 3)\n",
    )
    git(source, "add", "value.py", "test_value.py")
    git(
        source,
        "-c",
        "user.name=Replay fixture",
        "-c",
        "user.email=replay@example.invalid",
        "commit",
        "-qm",
        "Initialize disposable replay source",
    )
    atomic_write(marker, canonical({"replay": 1, "key": KEY}))


def execute(root, image, interrupt=False):
    workflow = Workflow(root / "state", image=image)
    if interrupt:
        # The coding receipt is durable before candidate intake starts.
        Workbench.register = lambda *_args, **_kwargs: os._exit(86)
    return workflow.run(
        KEY,
        root / "source",
        "Set the value to three. Preserve the existing public test.",
        ["value.py"],
        Recipe(image=PYTHON_IMAGE, argv=["python", "-m", "unittest", "discover", "-v"]),
        ReplayGateway(),
        timeout=300,
        max_requests=20,
        max_total_tokens=250000,
        max_repairs=1,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="A new directory outside the checkout")
    parser.add_argument("--image", default=os.environ.get("SWE_PLATFORM_AGENT_IMAGE"))
    parser.add_argument("--interrupt-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.image:
        parser.error("Build the Flue image and set SWE_PLATFORM_AGENT_IMAGE")
    root = args.directory.resolve()
    initialize(root)
    if args.interrupt_child:
        execute(root, args.image, interrupt=True)
        return
    workflow = Workflow(root / "state", image=args.image)
    directory = workflow.directory(KEY)
    if not (directory / "job.json").exists():
        process = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                str(root),
                "--image",
                args.image,
                "--interrupt-child",
            ],
            check=False,
            timeout=120,
        )
        if process.returncode != 86:
            raise RuntimeError("Replay did not reach its expected interruption boundary")
    result = execute(root, args.image)
    assert result["state"] == "ready_local" and result["repairs_used"] == 1
    assert result["requests_used_or_reserved"] == 5
    first = result["steps"]["candidate-0"]["result"]["candidate"]
    bench = Workbench(directory / "evidence")
    assert bench.inspect(first)["state"] == "stale"
    assert execute(root, args.image) == result
    assert (root / "source/value.py").read_text() == "value = 1\n"
    bundle = write_bundle(directory, root / "evidence.json")
    summary = {
        "scenario": "deterministic-flue-replay/v1",
        "state": result["state"],
        "process_interruption_recovered": True,
        "repairs": result["repairs_used"],
        "fixture_requests": result["requests_used_or_reserved"],
        "completed_resume_reused_receipts": True,
        "candidate_sha256": result["candidate"]["candidate"],
        "evidence_bundle": bundle,
    }
    atomic_write(root / "result.json", canonical(summary))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
