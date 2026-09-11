import base64
import json
import os
import stat
import subprocess
from pathlib import Path

from ..io import Artifacts, atomic_write, canonical, digest
from ..sandbox.docker import safe_path

MAX_BYTES = 8 * 1024 * 1024
MAX_FILES = 2000


def git(root, *args, data=None):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(root),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
        cwd=root,
        env=env,
        input=data,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout


def snapshot(source: Path, destination: Path):
    if git(source, "status", "--porcelain", "--untracked-files=normal").strip():
        raise ValueError("Input repository is dirty; commit or select a clean snapshot separately")
    revision = git(source, "rev-parse", "HEAD").decode().strip()
    entries = git(source, "ls-tree", "-rz", "--full-tree", revision).split(b"\0")
    if len(entries) > MAX_FILES + 1:
        raise ValueError("Too many source files")
    files = {}
    total = 0
    for entry in entries:
        if not entry:
            continue
        meta, raw_name = entry.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        name = safe_path(raw_name.decode())
        if mode not in ("100644", "100755") or kind != "blob":
            raise ValueError("Symlinks and submodules require an unsupported execution profile")
        if name == ".gitmodules" or Path(name).name == ".env":
            raise ValueError("Submodule or credential input is unsupported")
        size = int(git(source, "cat-file", "-s", sha))
        total += size
        if total > MAX_BYTES:
            raise ValueError("Source exceeds the snapshot size limit")
        data = git(source, "cat-file", "blob", sha)
        if data.startswith(b"version https://git-lfs.github.com/spec/"):
            raise ValueError("Git LFS sources are unsupported")
        files[name] = {"data": base64.b64encode(data).decode(), "mode": int(mode, 8) & 0o777}
    if git(source, "rev-parse", "HEAD").decode().strip() != revision:
        raise ValueError("Source revision changed during snapshot")
    materialize(destination, files)
    git(destination, "init", "-q", "--template=")
    for name, value in files.items():
        sha = (
            git(destination, "hash-object", "-w", "--stdin", data=base64.b64decode(value["data"]))
            .decode()
            .strip()
        )
        git(
            destination,
            "update-index",
            "--add",
            "--cacheinfo",
            f"100{value['mode']:o},{sha},{name}",
        )
    return {
        "base_revision": revision,
        "files": files,
        "tree_sha256": digest(canonical(files)),
        "source": str(source.resolve()),
    }


def materialize(root: Path, files):
    if root.exists():
        raise ValueError("Snapshot destination must be new")
    root.mkdir(parents=True, mode=0o700)
    for name, value in files.items():
        safe_path(name)
        if value["mode"] not in (0o644, 0o755):
            raise ValueError("Invalid file mode")
        path = root / name
        atomic_write(path, base64.b64decode(value["data"], validate=True))
        path.chmod(value["mode"])


def seal(root: Path, artifacts: Artifacts, base_revision: str, allowed_paths):
    files = {}
    total = 0
    for folder, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = [d for d in directories if not (Path(folder) == root and d == ".git")]
        for directory in directories:
            if (Path(folder) / directory).is_symlink():
                raise ValueError("Symlink directory in candidate")
        for filename in filenames:
            path = Path(folder) / filename
            name = safe_path(path.relative_to(root).as_posix())
            meta = path.lstat()
            if not stat.S_ISREG(meta.st_mode):
                raise ValueError("Non-regular candidate file")
            # Ignore build outputs according to the baseline Git ignore policy.
            tracked = bool(git(root, "ls-files", "--", name))
            if not tracked and name not in allowed_paths:
                continue
            total += meta.st_size
            if total > MAX_BYTES:
                raise ValueError("Candidate too large")
            files[name] = {
                "data": base64.b64encode(path.read_bytes()).decode(),
                "mode": 0o755 if meta.st_mode & 0o111 else 0o644,
            }
    # Use a disposable index: never let candidate .git configuration determine the patch.
    baseline = git(root, "ls-files", "-z").decode().rstrip("\0").split("\0")
    changed = set(
        git(root, "diff", "--no-ext-diff", "--no-textconv", "--name-only").decode().splitlines()
    )
    changed |= set(files) - set(baseline)
    if changed - set(allowed_paths):
        raise ValueError("Candidate changed paths outside the allowed scope")
    for name in set(files) - set(baseline):
        git(root, "add", "-N", "--", name)
    patch = git(root, "diff", "--binary", "--no-ext-diff", "--no-textconv")
    manifest = {
        "schema_version": "candidate/v1",
        "base_revision": base_revision,
        "tree_sha256": digest(canonical(files)),
        "files": files,
        "changed_paths": sorted(changed),
        "patch_sha256": artifacts.put(patch),
    }
    sha = artifacts.put(canonical(manifest))
    return sha, manifest


def load_candidate(artifacts: Artifacts, sha):
    candidate = json.loads(artifacts.get(sha))
    if digest(canonical(candidate["files"])) != candidate["tree_sha256"]:
        raise ValueError("Candidate tree mismatch")
    artifacts.get(candidate["patch_sha256"])
    for name in candidate["files"]:
        safe_path(name)
    return candidate
