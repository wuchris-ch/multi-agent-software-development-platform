"""Bounded policy search, frozen paired evaluation, and compare-and-swap rollout."""

import json
import time
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .io import atomic_write, canonical, digest, lock
from .models import StrictModel
from .policy import DevelopmentPolicy
from .producer import BINDINGS, IDENTITY, immutable, validate_wire
from .telemetry import execution_trace


class GateCase(StrictModel):
    task_id: str = Field(min_length=1, max_length=100)
    family: str = Field(min_length=1, max_length=100)
    split: Literal["development", "held_out"]
    trials: int = Field(default=3, ge=1, le=20)


class PromotionGate(StrictModel):
    schema_version: Literal["development-promotion-gate/v1"] = "development-promotion-gate/v1"
    cohort_id: str = Field(min_length=1, max_length=100)
    baseline_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cases: list[GateCase] = Field(min_length=2, max_length=20)
    minimum_development_gain: int = Field(default=1, ge=0)

    @model_validator(mode="after")
    def separate_families(self):
        development = {case.family for case in self.cases if case.split == "development"}
        held_out = {case.family for case in self.cases if case.split == "held_out"}
        if not development or not held_out or development & held_out:
            raise ValueError("Reserve disjoint development and held-out task families")
        if len({case.task_id for case in self.cases}) != len(self.cases):
            raise ValueError("Gate task identities must be distinct")
        if self.baseline_sha256 == self.candidate_sha256:
            raise ValueError("Promotion must compare distinct policies")
        if sum(case.trials for case in self.cases) > 120:
            raise ValueError("Gate exceeds the trial limit")
        return self


class PolicyRegistry:
    def __init__(self, root: Path):
        self.root = Path(root) / "policy-registry"
        self.workflows = Path(root) / "workflows"

    def register(self, policy):
        with lock(self.root / "control.lock", timeout=15):
            immutable(self.root / "policies" / (policy.sha256 + ".json"), policy.model_dump())
        return policy.sha256

    def policy(self, sha):
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("Invalid policy identity")
        value = DevelopmentPolicy.model_validate_json(
            (self.root / "policies" / (sha + ".json")).read_bytes()
        )
        if value.sha256 != sha:
            raise ValueError("Policy content changed")
        return value

    def active(self):
        path = self.root / "active.json"
        return json.loads(path.read_bytes()) if path.exists() else None

    def initialize(self, policy):
        sha = self.register(policy)
        with lock(self.root / "control.lock", timeout=15):
            current = self.active()
            if current and current["policy_sha256"] != sha:
                raise ValueError("An active policy already exists")
            if not current:
                current = {
                    "schema_version": "development-policy-rollout/v1",
                    "policy_sha256": sha,
                    "generation": 1,
                    "events": [{"at": time.time(), "action": "initialize", "to": sha}],
                }
                atomic_write(self.root / "active.json", canonical(current))
            return current

    def propose(self, parent_sha):
        parent = self.policy(parent_sha)
        failures, evidence = Counter(), []
        # Split is checked before reading any run trace, check output, or review.
        for path in sorted(self.workflows.glob("*/producer/admission.json")):
            admission = json.loads(path.read_bytes())
            if admission["ticket"]["trial_identity"]["split"] != "development":
                continue
            directory = path.parent.parent
            job = directory / "job.json"
            if not job.exists():
                continue
            record = json.loads(job.read_bytes())
            if record["request"].get("policy_sha256") != parent_sha:
                continue
            kinds = set()
            for name, step in record["steps"].items():
                result = step.get("result", {})
                if name.startswith("verify-") and result.get("passed") is False:
                    kinds.add("public_check_failure")
                if name.startswith("reviewer-") and "message" in result:
                    try:
                        if json.loads(result["message"]).get("blocked") is True:
                            kinds.add("blocking_review")
                    except ValueError:
                        pass
            if not kinds:
                continue
            failures.update(kinds)
            evidence.append(
                {
                    "execution_id": admission["ticket"]["execution_id"],
                    "trace_sha256": digest(canonical(execution_trace(directory))),
                    "failure_kinds": sorted(kinds),
                }
            )
        recurring = [kind for kind, count in failures.most_common() if count >= 2]
        if not recurring:
            return {"state": "insufficient_recurring_evidence", "development_runs": len(evidence)}
        hints = {
            "public_check_failure": "Before concluding, run the frozen public verification command and inspect failures. Check boundary cases implicated by the task. Preserve the verifier and fix the implementation.",
            "blocking_review": "Before concluding, inspect each changed function's callers, input validation and state transitions for concrete correctness defects. Fix demonstrated defects without broadening the change.",
        }
        kind = recurring[0]
        candidate = parent.model_copy(
            update={"name": "candidate-" + parent.sha256[:12], "coding_guidance": hints[kind]}
        )
        if candidate.coding_guidance == parent.coding_guidance:
            return {"state": "candidate_already_explored", "development_runs": len(evidence)}
        sha = self.register(candidate)
        proposal = {
            "schema_version": "development-policy-proposal/v1",
            "parent_sha256": parent_sha,
            "candidate_sha256": sha,
            "failure_kind": kind,
            "observed_runs": failures[kind],
            "evidence": evidence,
        }
        proposal_sha = digest(canonical(proposal))
        with lock(self.root / "control.lock", timeout=15):
            immutable(self.root / "proposals" / (proposal_sha + ".json"), proposal)
        return {"state": "proposed", "proposal_sha256": proposal_sha, **proposal}

    def freeze(self, gate: PromotionGate):
        self.policy(gate.baseline_sha256)
        self.policy(gate.candidate_sha256)
        sha = digest(canonical(gate.model_dump()))
        with lock(self.root / "control.lock", timeout=15):
            immutable(self.root / "gates" / sha / "gate.json", gate.model_dump())
            path = self.root / "gates" / sha / "admission.json"
            if not path.exists():
                atomic_write(path, canonical({"created_at": time.time()}))
        return {
            "gate_sha256": sha,
            "expected_executions": 2 * sum(case.trials for case in gate.cases),
        }

    def load_gate(self, sha):
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("Invalid gate identity")
        root = self.root / "gates" / sha
        gate = PromotionGate.model_validate_json((root / "gate.json").read_bytes())
        if digest(canonical(gate.model_dump())) != sha:
            raise ValueError("Frozen promotion gate changed")
        return root, gate

    def evaluate(self, gate_sha, split):
        if split not in ("development", "held_out"):
            raise ValueError("Unknown gate split")
        root, gate = self.load_gate(gate_sha)
        cutoff = json.loads((root / "admission.json").read_bytes())["created_at"]
        if split == "held_out":
            development = json.loads((root / "development.json").read_bytes())
            if development["state"] != "passed":
                raise ValueError("Development evaluation must pass before held-out evaluation")
            cutoff = development["evaluated_at"]
        expected = {
            (case.task_id, trial, arm): case
            for case in gate.cases
            if case.split == split
            for trial in range(1, case.trials + 1)
            for arm in ("baseline", "candidate")
        }
        observations, admitted = {}, set()
        for path in sorted(self.workflows.glob("*/producer/admission.json")):
            admission = json.loads(path.read_bytes())
            ticket = admission["ticket"]
            trial = ticket["trial_identity"]
            if trial["cohort_id"] != gate.cohort_id or trial["split"] != split:
                continue
            # Assisted executions remain in the journal, outside the initial paired denominator.
            if trial["attempt_kind"] != "initial":
                continue
            key = (trial["task_id"], trial["trial"], trial["arm"])
            if key not in expected or trial["family"] != expected[key].family:
                raise ValueError("Trial is outside the frozen comparison")
            if key in admitted:
                raise ValueError("Comparison contains duplicate attempts")
            admitted.add(key)
            directory = path.parent.parent
            if not (directory / "job.json").exists():
                continue
            record = json.loads((directory / "job.json").read_bytes())
            policy_sha = (
                gate.baseline_sha256 if trial["arm"] == "baseline" else gate.candidate_sha256
            )
            if (
                record["request"].get("policy_sha256") != policy_sha
                or record["events"][0]["at"] < cutoff
            ):
                raise ValueError("Trial policy or admission time differs from the frozen gate")
            recipe = admission["recipe"]
            if digest(canonical(recipe)) != ticket["recipe_sha256"]:
                raise ValueError("Trial production recipe changed")
            if recipe["budget"]["max_repairs"] != 0:
                raise ValueError("Initial comparison trials must disable internal repairs")
            assessment_path = directory / "producer/assessment.json"
            outcome, assessment_sha = None, None
            if assessment_path.exists():
                assessment = validate_wire("Assessment", json.loads(assessment_path.read_bytes()))
                submission = validate_wire(
                    "Submission", json.loads((directory / "producer/submission.json").read_bytes())
                )
                if (
                    any(
                        assessment[field] != submission[field]
                        for field in (*IDENTITY, *BINDINGS, "execution_contract_sha256")
                    )
                    or assessment["submission_sha256"] != digest(canonical(submission))
                    or submission["trial_ticket_sha256"] != digest(canonical(ticket))
                ):
                    raise ValueError("Comparison assessment binding changed")
                if record["candidates"][-1] != assessment["candidate_manifest_sha256"]:
                    raise ValueError("Comparison uses a superseded candidate")
                if any(
                    assessment[field] != ticket[field]
                    for field in (
                        "execution_id",
                        "recipe_sha256",
                        "suite_sha256",
                        "policy_sha256",
                        "evaluator_sha256",
                    )
                ):
                    raise ValueError("Assessment differs from the reserved trial")
                outcome = assessment["outcome"]
                if outcome == "pass" and (
                    not assessment["checks"]
                    or not all(check["passed"] for check in assessment["checks"])
                ):
                    raise ValueError("Passing assessment contradicts its checks")
                assessment_sha = digest(canonical(assessment))
            elif record["state"] in ("cancelled", "needs_attention"):
                outcome = "production_failed"
            observations[key] = {
                "execution_id": ticket["execution_id"],
                "outcome": outcome,
                "assessment_sha256": assessment_sha,
                "match": {
                    field: recipe[field]
                    for field in ("capability_sha256", "model_configuration_sha256", "budget")
                },
                "authority": {
                    field: ticket[field]
                    for field in (
                        "base_revision",
                        "suite_sha256",
                        "policy_sha256",
                        "evaluator_sha256",
                    )
                },
            }
        rows, missing = [], []
        for key, case in expected.items():
            if key[2] != "baseline":
                continue
            candidate_key = (key[0], key[1], "candidate")
            pair = [observations.get(key), observations.get(candidate_key)]
            if any(value is None or value["outcome"] is None for value in pair):
                missing.append({"task_id": key[0], "trial": key[1]})
                continue
            baseline, candidate = pair
            if (
                baseline["match"] != candidate["match"]
                or baseline["authority"] != candidate["authority"]
            ):
                raise ValueError(
                    "Paired trials differ in model, capability, budget or evaluator authority"
                )
            rows.append(
                {
                    "task_id": key[0],
                    "family": case.family,
                    "trial": key[1],
                    "baseline": baseline,
                    "candidate": candidate,
                }
            )
        base_pass = sum(row["baseline"]["outcome"] == "pass" for row in rows)
        candidate_pass = sum(row["candidate"]["outcome"] == "pass" for row in rows)
        regressions = sum(
            row["baseline"]["outcome"] == "pass" and row["candidate"]["outcome"] != "pass"
            for row in rows
        )
        minimum = gate.minimum_development_gain if split == "development" else 0
        passed = (
            not missing
            and not regressions
            and candidate_pass > 0
            and candidate_pass - base_pass >= minimum
        )
        report = {
            "schema_version": "development-policy-comparison/v1",
            "gate_sha256": gate_sha,
            "split": split,
            "state": "incomplete" if missing else "passed" if passed else "rejected",
            "expected_pairs": len(expected) // 2,
            "completed_pairs": len(rows),
            "baseline_passes": base_pass,
            "candidate_passes": candidate_pass,
            "regressions": regressions,
            "missing": missing,
            "pairs": rows,
            "evaluated_at": time.time(),
        }
        if not missing:
            with lock(self.root / "control.lock", timeout=15):
                path = root / (split + ".json")
                if path.exists():
                    previous = json.loads(path.read_bytes())
                    report["evaluated_at"] = previous["evaluated_at"]
                immutable(path, report)
        return report

    def promote(self, gate_sha, expected_sha, expected_generation):
        root, gate = self.load_gate(gate_sha)
        if gate.baseline_sha256 != expected_sha:
            raise ValueError("Promotion baseline differs from the expected active policy")
        evidence = []
        for split in ("development", "held_out"):
            report = self.evaluate(gate_sha, split)
            if report["state"] != "passed":
                raise ValueError("Both frozen evaluations must pass before promotion")
            evidence.append(digest(canonical(report)))
        return self._change(
            gate.candidate_sha256,
            expected_sha,
            "promote",
            {"gate_sha256": gate_sha, "comparison_sha256": evidence},
            expected_generation,
        )

    def rollback(self, target_sha, expected_sha, expected_generation):
        active = self.active()
        if not active or target_sha not in {event["to"] for event in active["events"]}:
            raise ValueError("Rollback target must be a previously active policy")
        return self._change(target_sha, expected_sha, "rollback", {}, expected_generation)

    def _change(self, target, expected, action, evidence, expected_generation):
        self.policy(target)
        with lock(self.root / "control.lock", timeout=15):
            active = self.active()
            if (
                not active
                or active["policy_sha256"] != expected
                or active["generation"] != expected_generation
            ):
                raise ValueError("Active policy changed; refresh before rollout")
            active["events"].append(
                {"at": time.time(), "action": action, "from": expected, "to": target, **evidence}
            )
            active["policy_sha256"] = target
            active["generation"] += 1
            atomic_write(self.root / "active.json", canonical(active))
            return active
