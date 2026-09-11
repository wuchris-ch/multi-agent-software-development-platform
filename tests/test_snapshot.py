import base64
import subprocess

import pytest

from swe_platform.io import Artifacts
from swe_platform.sandbox.docker import safe_path
from swe_platform.workspace.snapshot import seal, snapshot


@pytest.fixture
def repository(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    # Fixture-only object creation without a commit command or author identity in the project.
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "calc.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(source), "add", "calc.py"], check=True)
    tree = subprocess.check_output(["git", "-C", str(source), "write-tree"]).strip()
    commit = (
        b"tree "
        + tree
        + b"\nauthor Fixture <fixture@invalid> 0 +0000\ncommitter Fixture <fixture@invalid> 0 +0000\n\nfixture\n"
    )
    sha = subprocess.check_output(
        ["git", "-C", str(source), "hash-object", "-t", "commit", "-w", "--stdin"], input=commit
    ).strip()
    subprocess.run(["git", "-C", str(source), "update-ref", "HEAD", sha], check=True)
    return source


def test_snapshot_and_seal_includes_add_delete_and_scope(repository, tmp_path):
    destination = tmp_path / "candidate"
    base = snapshot(repository, destination)
    (destination / "calc.py").unlink()
    (destination / "new.py").write_text("value = 2\n")
    artifacts = Artifacts(tmp_path / "artifacts")
    sha, candidate = seal(destination, artifacts, base["base_revision"], ["calc.py", "new.py"])
    assert "calc.py" not in candidate["files"]
    assert base64.b64decode(candidate["files"]["new.py"]["data"]) == b"value = 2\n"
    patch = artifacts.get(candidate["patch_sha256"])
    assert b"deleted file" in patch and b"new file" in patch
    assert (repository / "calc.py").read_text() == "value = 1\n"
    assert artifacts.get(sha)


def test_snapshot_rejects_dirty_and_seal_symlinks(repository, tmp_path):
    (repository / "untracked").touch()
    with pytest.raises(ValueError, match="dirty"):
        snapshot(repository, tmp_path / "no")
    (repository / "untracked").unlink()
    destination = tmp_path / "candidate"
    base = snapshot(repository, destination)
    (destination / "calc.py").write_text("changed")
    with pytest.raises(ValueError, match="scope"):
        seal(destination, Artifacts(tmp_path / "artifacts"), base["base_revision"], [])
    (destination / "escape").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="Non-regular"):
        seal(
            destination,
            Artifacts(tmp_path / "artifacts"),
            base["base_revision"],
            ["calc.py", "escape"],
        )


@pytest.mark.parametrize(
    "path", ["../secret", "/etc/passwd", "a/../b", "a//b", ".git/config", ".", "a\\b"]
)
def test_unsafe_paths(path):
    with pytest.raises(ValueError):
        safe_path(path)
