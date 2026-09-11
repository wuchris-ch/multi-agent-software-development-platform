import json
import shutil
import subprocess

import pytest
from test_snapshot import repository  # noqa: F401

from swe_platform.candidates import Recipe, Workbench
from swe_platform.credentials import GatewayProfile
from swe_platform.io import atomic_write, canonical
from swe_platform.sandbox.docker import PYTHON_IMAGE, Docker

PATCH = b"diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"


def test_workbench_exact_evidence_and_idempotency(repository, tmp_path, monkeypatch):  # noqa: F811
    if subprocess.run(["docker", "info"], capture_output=True).returncode:
        pytest.skip("Docker unavailable")
    workbench = Workbench(tmp_path / "state")
    recipe = Recipe(
        image=PYTHON_IMAGE, argv=["python", "-c", "import calc; assert calc.value == 2"]
    )
    intake = workbench.intake(repository, PATCH, ["calc.py"], recipe)
    sha = intake["candidate"]
    assert workbench.inspect(sha)["state"] == "needs_attention"
    checked = workbench.verify(sha)
    assert checked["passed"]
    monkeypatch.setattr(Docker, "run", lambda *_a, **_kw: pytest.fail("duplicate verification"))
    assert workbench.verify(sha) == checked
    # A trusted fake executable proves the adapter path without a paid model call.
    cli = tmp_path / "cli.js"
    cli.write_text(
        "const fs=require('fs'),c=require('crypto'); const diff=fs.readFileSync(0); console.log(JSON.stringify({schema_version:'1.0',input_sha256:c.createHash('sha256').update(diff).digest('hex'),blocked:false,risk:'low',findings:[],rationale:'Fixture verdict'}));"
    )
    monkeypatch.setattr(GatewayProfile, "environment", lambda _self: {})
    profile = GatewayProfile(
        base_url="https://example.invalid", model="fixture", keychain_service="fixture"
    )
    workbench.review(sha, cli, profile, shutil.which("node"))
    assert workbench.inspect(sha)["state"] == "ready_local"
    receipt = workbench.root / sha / "review.json"
    old = json.loads(receipt.read_bytes())
    old["candidate"] = "0" * 64
    atomic_write(receipt, canonical(old))
    with pytest.raises(ValueError, match="match the candidate"):
        workbench.inspect(sha)


def test_private_profile_validation_hides_input():
    with pytest.raises(ValueError) as exc:
        GatewayProfile(
            base_url="https://user:SECRET-CANARY@example.invalid",
            model="fixture",
            keychain_service="fixture",
        )
    assert "SECRET-CANARY" not in str(exc.value)


def test_manual_repairs_are_bounded_and_invalidate_evidence(repository, tmp_path):  # noqa: F811
    workbench = Workbench(tmp_path / "state")
    recipe = Recipe(image=PYTHON_IMAGE, argv=["true"])
    first = workbench.intake(repository, PATCH, ["calc.py"], recipe)["candidate"]
    with pytest.raises(ValueError, match="identical"):
        workbench.repair(first, PATCH)
    second = workbench.repair(first, PATCH.replace(b"+value = 2", b"+value = 3"))["candidate"]
    assert workbench.inspect(first)["state"] == "stale"
    assert workbench.inspect(second)["evidence"] == {}
    third = workbench.repair(second, PATCH.replace(b"+value = 2", b"+value = 4"))["candidate"]
    assert workbench.inspect(third)["repairs_used"] == 2
    with pytest.raises(ValueError, match="exhausted"):
        workbench.repair(third, PATCH.replace(b"+value = 2", b"+value = 5"))
