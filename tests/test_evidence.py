import base64
import json

import pytest
from test_console import OPERATOR, auth
from test_console import console as console
from test_github_publication import publication as publication
from test_snapshot import repository as repository

from swe_platform.evidence import export_workflow, verify_bundle
from swe_platform.io import canonical, digest


def test_portable_bundle_verifies_without_workspace_and_excludes_private_state(console, tmp_path):
    _, _, _, _, directory = console
    raw = export_workflow(directory)
    assert b"PRIVATE-PROVIDER-CANARY" not in raw
    assert b"/private/work" not in raw
    assert b"Fix retry handling" not in raw
    exported = tmp_path / "bundle.json"
    exported.write_bytes(raw)
    result = verify_bundle(exported.read_bytes(), expected_sha256=digest(raw))
    assert result["state"] == "verified" and result["artifact_count"] == 4
    assert result["changed_paths"] == ["calc.py"]
    with pytest.raises(ValueError, match="expected digest"):
        verify_bundle(raw, expected_sha256="0" * 64)


@pytest.mark.parametrize(
    "tamper", ["artifact", "candidate", "recipe", "review", "scope", "outcome", "extra", "trace"]
)
def test_offline_verification_rejects_substitution(console, tamper):
    _, _, _, _, directory = console
    body = json.loads(export_workflow(directory))
    if tamper == "artifact":
        body["artifacts"][body["candidate_sha256"]] = base64.b64encode(b"substituted").decode()
    elif tamper == "candidate":
        body["verification"]["candidate"] = "f" * 64
    elif tamper == "recipe":
        body["recipe"]["argv"] = ["echo", "weaker checks"]
    elif tamper == "review":
        body["review"]["input_sha256"] = "f" * 64
    elif tamper == "scope":
        body["allowed_paths"] = []
    elif tamper == "outcome":
        body["verification"]["exit_code"] = 1
    elif tamper == "trace":
        trace = json.loads(base64.b64decode(body["artifacts"].pop(body["trace_sha256"])))
        trace["workflow_id"] = "f" * 64
        data = canonical(trace)
        body["trace_sha256"] = digest(data)
        body["artifacts"][digest(data)] = base64.b64encode(data).decode()
    else:
        body["artifacts"][digest(b"unrelated")] = base64.b64encode(b"unrelated").decode()
    with pytest.raises(ValueError):
        verify_bundle(canonical(body))


def test_browser_download_preserves_bundle_bytes_and_pinned_hash(console):
    client, _, identifier, _, _ = console
    result = client.get(f"/api/workflows/{identifier}/bundle", headers=auth(OPERATOR))
    assert result.status_code == 200
    assert result.headers["content-disposition"].startswith(
        'attachment; filename="development-evidence-'
    )
    assert (
        verify_bundle(result.content, expected_sha256=result.headers["x-content-sha256"])["state"]
        == "verified"
    )
