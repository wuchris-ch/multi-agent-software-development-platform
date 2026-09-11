"""Trusted M0 fixture only. Never execute user-provided repositories on the host."""

import subprocess
import sys
from pathlib import Path

from .io import Artifacts, atomic_write, canonical, digest

BASE = b"def add(a, b):\n    return a - b\n"
FIXED = b"def add(a, b):\n    return a + b\n"
CHECK = "from calculator import add; assert add(2, 3) == 5; assert add(-2, 3) == 1"


def git(path: Path, *args) -> bytes:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(path),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=path,
        env=env,
        check=True,
        capture_output=True,
        timeout=15,
    ).stdout


def prepare(path: Path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (path / ".prepared").exists():
        return
    atomic_write(path / "calculator.py", BASE)
    git(path, "init", "-q")
    git(path, "add", "calculator.py")
    # Local fixture object only, no commit command and no attribution metadata.
    tree = git(path, "write-tree").strip()
    atomic_write(path / ".prepared", tree)


def verify(path: Path, artifacts: Artifacts):
    code = (path / "calculator.py").read_bytes()
    if code not in (BASE, FIXED):
        raise ValueError("Scripted fixture produced unexpected source")
    patch = git(path, "diff", "--no-ext-diff", "--", "calculator.py")
    candidate = artifacts.put(canonical({"calculator.py": digest(code)}))
    patch_sha = artifacts.put(patch)
    source_sha = artifacts.put(code)
    # Independent fresh snapshot, restricted to two known safe fixture implementations.
    snapshot = path.parent / "verification"
    snapshot.mkdir(exist_ok=True, mode=0o700)
    atomic_write(snapshot / "calculator.py", code)
    command = [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, '.') ; " + CHECK]
    run = subprocess.run(
        command, cwd=snapshot, env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=10
    )
    report = {
        "schema_version": "1.0",
        "candidate": candidate,
        "patch": patch_sha,
        "source": source_sha,
        "recipe": digest(CHECK.encode()),
        "passed": run.returncode == 0,
        "exit_code": run.returncode,
        "output": run.stderr.decode()[:4096],
        "review": "not_required_scripted_fixture",
    }
    return candidate, artifacts.put(canonical(report)), report
