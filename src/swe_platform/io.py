import fcntl
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def lock(path: Path, *, timeout=0):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+") as file:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        try:
            yield file
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def locked(path: Path) -> bool:
    try:
        with lock(path):
            return False
    except BlockingIOError:
        return True


class Artifacts:
    def __init__(self, root: Path):
        self.root = root

    def put(self, data: bytes) -> str:
        sha = digest(data)
        path = self.root / sha
        if not path.exists():
            atomic_write(path, data)
            path.chmod(0o400)
        self.get(sha)
        return sha

    def get(self, sha: str) -> bytes:
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("Invalid artifact digest")
        data = (self.root / sha).read_bytes()
        if digest(data) != sha:
            raise ValueError("Artifact digest mismatch")
        return data
