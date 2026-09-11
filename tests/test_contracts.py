import time

import pytest

from swe_platform.evaluation import (
    ArtifactReference,
    Decision,
    Submission,
    validate_decision,
    validate_submission,
)
from swe_platform.io import Artifacts, canonical, digest
from swe_platform.publication import FakeGitHub, PublicationPlan, SimulationAuthorization, simulate


def test_evaluator_candidate_binding_and_artifact_tampering(tmp_path):
    artifacts = Artifacts(tmp_path)
    sha = artifacts.put(b"patch")
    submission = Submission(
        execution_id="issued",
        producer_run_id="job",
        base_revision="a" * 40,
        candidate_tree_sha256="b" * 64,
        recipe_sha256="c" * 64,
        artifacts=[ArtifactReference(role="patch", sha256=sha, storage_key=sha)],
    )
    submission_sha = validate_submission(submission, artifacts, "issued")
    decision = Decision(
        execution_id="issued",
        submission_sha256=submission_sha,
        assessment_set_sha256="d" * 64,
        policy_sha256="e" * 64,
        evaluator_revision="f" * 40,
        outcome="pass",
    )
    assert (
        validate_decision(decision, submission, policy_sha256="e" * 64, evaluator_revision="f" * 40)
        == "pass"
    )
    with pytest.raises(ValueError):
        validate_decision(
            decision,
            submission.model_copy(update={"candidate_tree_sha256": "1" * 64}),
            policy_sha256="e" * 64,
            evaluator_revision="f" * 40,
        )
    with pytest.raises(ValueError):
        validate_submission(submission, artifacts, "another")
    (tmp_path / sha).chmod(0o600)
    (tmp_path / sha).write_bytes(b"different")
    with pytest.raises(ValueError):
        validate_submission(submission, artifacts, "issued")


def test_publication_is_offline_exact_and_reconciles_lost_response():
    plan = PublicationPlan(
        job_id="job",
        repository="owner/repo",
        branch="agent/job-fixture",
        base_sha="a" * 40,
        head_sha="b" * 40,
        candidate_sha256="c" * 64,
        title="Fixture change",
        body="Local evidence",
    )
    authorization = SimulationAuthorization(
        plan_sha256=digest(canonical(plan.model_dump())), expires_at=time.time() + 60
    )
    remote = FakeGitHub(plan.base_sha, plan.head_sha, lose_response=True)
    ledger = {}
    assert simulate(plan, authorization, remote, ledger)["state"] == "ambiguous"
    assert simulate(plan, authorization, remote, ledger)["state"] == "confirmed"
    assert remote.posts == 1
    remote.head_sha = "d" * 40
    assert simulate(plan, authorization, remote, ledger)["state"] == "stale"
    with pytest.raises(ValueError):
        simulate(plan.model_copy(update={"title": "changed"}), authorization, remote, ledger)
    with pytest.raises(ValueError):
        simulate(plan, authorization, object(), ledger)
