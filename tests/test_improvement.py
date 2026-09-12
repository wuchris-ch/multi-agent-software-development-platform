import json
import shutil
import time
import uuid
from pathlib import Path

import pytest

from swe_platform.improvement import PolicyRegistry, PromotionGate
from swe_platform.io import atomic_write, canonical, digest
from swe_platform.policy import DevelopmentPolicy


def template(name):
    return json.loads(
        (Path(__file__).parent / "fixtures/evaluator-v2" / (name + ".json")).read_bytes()
    )


def setup_gate(root):
    registry = PolicyRegistry(root)
    baseline = DevelopmentPolicy.preset("review")
    candidate = baseline.model_copy(
        update={"name": "candidate", "coding_guidance": "Check all callers."}
    )
    registry.initialize(baseline)
    registry.register(candidate)
    gate = PromotionGate(
        cohort_id="paired-control",
        baseline_sha256=baseline.sha256,
        candidate_sha256=candidate.sha256,
        cases=[
            {"task_id": "api", "family": "api", "split": "development", "trials": 2},
            {"task_id": "storage", "family": "storage", "split": "held_out", "trials": 1},
        ],
    )
    sha = registry.freeze(gate)["gate_sha256"]
    return registry, gate, sha


def trial(root, gate, *, split, number, arm, outcome, capability="c" * 64):
    case = next(case for case in gate.cases if case.split == split)
    execution = str(uuid.uuid4())
    key = case.task_id + str(number) + arm
    directory = root / "workflows" / digest(key.encode())
    ticket = template("ticket")
    ticket.update(
        execution_id=execution,
        trial_identity={
            "cohort_id": gate.cohort_id,
            "task_id": case.task_id,
            "family": case.family,
            "split": split,
            "trial": number,
            "arm": arm,
            "attempt_kind": "initial",
            "parent_execution_id": None,
        },
    )
    recipe = {
        "capability_sha256": capability,
        "model_configuration_sha256": "d" * 64,
        "budget": {
            "max_model_requests": 30,
            "max_total_tokens": 250000,
            "max_elapsed_seconds": 600,
            "max_repairs": 0,
        },
    }
    ticket["recipe_sha256"] = digest(canonical(recipe))
    submission = template("submission")
    for field in (
        "execution_id",
        "recipe_sha256",
        "suite_sha256",
        "policy_sha256",
        "evaluator_sha256",
    ):
        submission[field] = ticket[field]
    submission["trial_ticket_sha256"] = digest(canonical(ticket))
    assessment = template("assessment")
    assessment.update(
        {
            key: value
            for key, value in submission.items()
            if key in assessment and key != "schema_version"
        }
    )
    assessment["submission_sha256"] = digest(canonical(submission))
    assessment["outcome"] = outcome
    assessment["checks"] = [
        {"id": "behavior", "passed": outcome == "pass", "reason": "controlled observation"}
    ]
    policy = gate.baseline_sha256 if arm == "baseline" else gate.candidate_sha256
    record = {
        "key": key,
        "request": {"policy_sha256": policy},
        "state": "ready_local",
        "events": [{"seq": 1, "at": time.time(), "event": "workflow.created"}],
        "steps": {},
        "candidates": [assessment["candidate_manifest_sha256"]],
    }
    atomic_write(directory / "job.json", canonical(record))
    atomic_write(
        directory / "producer/admission.json", canonical({"ticket": ticket, "recipe": recipe})
    )
    atomic_write(directory / "producer/submission.json", canonical(submission))
    atomic_write(directory / "producer/assessment.json", canonical(assessment))
    return directory


def development(root, gate):
    for number in (1, 2):
        trial(
            root,
            gate,
            split="development",
            number=number,
            arm="baseline",
            outcome="fail" if number == 1 else "pass",
        )
        trial(root, gate, split="development", number=number, arm="candidate", outcome="pass")


def test_frozen_gate_requires_complete_pairs_then_held_out_and_supports_fenced_rollback(tmp_path):
    registry, gate, sha = setup_gate(tmp_path)
    initial = registry.evaluate(sha, "development")
    assert initial["state"] == "incomplete" and initial["expected_pairs"] == 2
    with pytest.raises((FileNotFoundError, ValueError)):
        registry.promote(sha, gate.baseline_sha256, 1)
    development(tmp_path, gate)
    assert registry.evaluate(sha, "development")["state"] == "passed"
    for arm in ("baseline", "candidate"):
        trial(tmp_path, gate, split="held_out", number=1, arm=arm, outcome="pass")
    assert registry.evaluate(sha, "held_out")["state"] == "passed"
    promoted = registry.promote(sha, gate.baseline_sha256, 1)
    assert promoted["policy_sha256"] == gate.candidate_sha256 and promoted["generation"] == 2
    reverted = registry.rollback(gate.baseline_sha256, gate.candidate_sha256, 2)
    assert reverted["generation"] == 3 and reverted["events"][-1]["action"] == "rollback"
    with pytest.raises(ValueError, match="Active policy changed"):
        registry.promote(sha, gate.baseline_sha256, 1)


def test_gate_rejects_contamination_budget_drift_and_regression(tmp_path):
    registry, gate, sha = setup_gate(tmp_path)
    with pytest.raises(ValueError, match="disjoint"):
        PromotionGate.model_validate(
            {
                **gate.model_dump(),
                "cases": [
                    {"task_id": "a", "family": "same", "split": "development"},
                    {"task_id": "b", "family": "same", "split": "held_out"},
                ],
            }
        )
    development(tmp_path, gate)
    registry.evaluate(sha, "development")
    trial(tmp_path, gate, split="held_out", number=1, arm="baseline", outcome="pass")
    directory = trial(
        tmp_path,
        gate,
        split="held_out",
        number=1,
        arm="candidate",
        outcome="fail",
        capability="e" * 64,
    )
    with pytest.raises(ValueError, match="Paired trials differ"):
        registry.evaluate(sha, "held_out")
    admission = json.loads((directory / "producer/admission.json").read_bytes())
    admission["recipe"]["capability_sha256"] = "c" * 64
    admission["ticket"]["recipe_sha256"] = digest(canonical(admission["recipe"]))
    # Rewrite this controlled candidate from its input, then establish a real regression.
    trial(tmp_path, gate, split="held_out", number=1, arm="candidate", outcome="fail")
    assert registry.evaluate(sha, "held_out")["state"] == "rejected"
    with pytest.raises(ValueError, match="Both frozen evaluations"):
        registry.promote(sha, gate.baseline_sha256, 1)


def test_policy_mining_never_reads_held_out_traces(tmp_path):
    registry, gate, _ = setup_gate(tmp_path)
    for number in (1, 2):
        directory = trial(
            tmp_path, gate, split="development", number=number, arm="baseline", outcome="fail"
        )
        job = directory / "job.json"
        record = json.loads(job.read_bytes())
        record["steps"] = {"verify-0": {"reserved_requests": 0, "result": {"passed": False}}}
        atomic_write(job, canonical(record))
    held = tmp_path / "workflows" / ("f" * 64)
    ticket = template("ticket")
    ticket["trial_identity"]["split"] = "held_out"
    atomic_write(held / "producer/admission.json", canonical({"ticket": ticket}))
    atomic_write(held / "job.json", b"DO NOT READ: withheld trace canary")
    result = registry.propose(gate.baseline_sha256)
    assert result["state"] == "proposed" and result["observed_runs"] == 2
    proposed = registry.policy(result["candidate_sha256"])
    assert proposed.mode == "review" and "frozen public verification" in proposed.coding_guidance
    assert registry.active()["policy_sha256"] == gate.baseline_sha256


def test_initial_gate_keeps_assisted_results_outside_its_denominator(tmp_path):
    registry, gate, sha = setup_gate(tmp_path)
    development(tmp_path, gate)
    initial = next((tmp_path / "workflows").glob("*/producer/admission.json"))
    assisted = json.loads(initial.read_bytes())
    assisted["ticket"]["trial_identity"]["attempt_kind"] = "assisted"
    assisted["ticket"]["trial_identity"]["parent_execution_id"] = assisted["ticket"]["execution_id"]
    assisted["ticket"]["execution_id"] = str(uuid.uuid4())
    atomic_write(
        tmp_path / "workflows" / ("0" * 64) / "producer/admission.json", canonical(assisted)
    )
    result = registry.evaluate(sha, "development")
    assert result["state"] == "passed" and result["completed_pairs"] == 2
    assert result["candidate_passes"] == 2 and result["baseline_passes"] == 1


def test_duplicate_admission_cannot_disappear_when_its_job_never_started(tmp_path):
    registry, gate, sha = setup_gate(tmp_path)
    development(tmp_path, gate)
    initial = next((tmp_path / "workflows").glob("*/producer/admission.json"))
    destination = tmp_path / "workflows" / ("0" * 64) / "producer/admission.json"
    destination.parent.mkdir(parents=True)
    shutil.copyfile(initial, destination)
    with pytest.raises(ValueError, match="duplicate attempts"):
        registry.evaluate(sha, "development")


def test_initial_comparison_rejects_recipes_that_hide_repairs(tmp_path):
    registry, gate, sha = setup_gate(tmp_path)
    directory = trial(tmp_path, gate, split="development", number=1, arm="baseline", outcome="pass")
    path = directory / "producer/admission.json"
    admission = json.loads(path.read_bytes())
    admission["recipe"]["budget"]["max_repairs"] = 1
    admission["ticket"]["recipe_sha256"] = digest(canonical(admission["recipe"]))
    atomic_write(path, canonical(admission))
    with pytest.raises(ValueError, match="disable internal repairs"):
        registry.evaluate(sha, "development")
