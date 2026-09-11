"""Fixed credential-free worker. Its receipt and lifetime lock prevent duplicate writers."""

import json
import os
import signal
import sys
import time
from pathlib import Path

from .fixture import BASE, FIXED
from .io import atomic_write, canonical, lock


def run(path: Path):
    try:
        with lock(path / "execution.lock"):
            # A durable tombstone also fences a process launched before cancellation
            # which was not scheduled by the OS until after cancellation completed.
            if (path / "cancel").exists() or (path / "started.json").exists():
                return
            spec = json.loads((path / "spec.json").read_text())
            atomic_write(
                path / "started.json",
                canonical(
                    {"pid": os.getpid(), "attempt_id": spec["attempt_id"], "epoch": spec["epoch"]}
                ),
            )
            if spec["behavior"] == "ignore_cancel":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            start = time.monotonic()
            outcome = "completed"
            while time.monotonic() - start < spec["delay_seconds"]:
                if (path / "cancel").exists() and spec["behavior"] != "ignore_cancel":
                    outcome = "cancelled"
                    break
                if time.time() > spec["deadline"]:
                    outcome = "timeout"
                    break
                time.sleep(0.02)
            if (path / "cancel").exists():
                outcome = "cancelled"
            if outcome == "completed":
                code = BASE if spec["behavior"] == "wrong" else FIXED
                atomic_write(path / "repo/calculator.py", code)
            atomic_write(
                path / "result.json",
                canonical(
                    {"attempt_id": spec["attempt_id"], "epoch": spec["epoch"], "outcome": outcome}
                ),
            )
    except BlockingIOError:
        return


if __name__ == "__main__":
    run(Path(sys.argv[1]))
