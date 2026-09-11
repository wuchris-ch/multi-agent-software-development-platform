"""Trusted entrypoint in the container; the entire container is disposable.

Output is untrusted candidate data, never an authoritative evaluation decision.
"""

import base64
import json
import os
import selectors
import signal
import stat
import subprocess
import time
from pathlib import Path

LIMIT = 256 * 1024


def run():
    spec = json.loads(Path("/input.json").read_bytes())
    root = Path("/work")
    os.chdir(root)
    for name, value in spec["files"].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(value["data"], validate=True))
        path.chmod(value["mode"])
    process = subprocess.Popen(
        spec["argv"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    selector.register(process.stderr, selectors.EVENT_READ)
    log = bytearray()
    reason = None
    deadline = min(spec["deadline"], time.time() + spec["timeout"])
    try:
        while selector.get_map():
            if time.time() >= deadline:
                reason = "timeout"
                break
            for key, _ in selector.select(0.05):
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    remaining = LIMIT - len(log)
                    log.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        reason = "output_limit"
                        break
            if reason:
                break
        if not reason:
            process.wait(timeout=max(0.01, deadline - time.time()))
    except subprocess.TimeoutExpired:
        reason = "timeout"
    finally:
        # Kill descendants even if the immediate command has already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        selector.close()
    result = {
        "exit_code": process.returncode,
        "reason": reason,
        "output": log.decode("utf-8", errors="replace"),
        "files": {},
    }
    total = 0
    for name in spec["collect"]:
        path = root / name
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root.parent):
            raise ValueError("symlink output")
        if not path.exists():
            result["files"][name] = None
            continue
        meta = path.stat()
        total += meta.st_size
        if not stat.S_ISREG(meta.st_mode) or total > LIMIT:
            raise ValueError("invalid or oversized output")
        result["files"][name] = {
            "data": base64.b64encode(path.read_bytes()).decode(),
            "mode": 0o755 if meta.st_mode & 0o111 else 0o644,
        }
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    run()
