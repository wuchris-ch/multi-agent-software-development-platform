# Evaluator integration boundary

Rechecked September 11, 2026 against the evaluator's active implementation worktree, based on `9b68ab520427e93a104a8d36966401a499f2fd92`. Uncommitted work now includes durable experiments, a workbench API/UI, environment observations, PostgreSQL claims, and Kubernetes execution. These are source observations; this project has not certified or deployed that implementation.

The evaluator extends its existing transports and graders. Its experiment journal distinguishes saved observations from ambiguous dispatch: a recorded observation can be graded again; dispatch without a receipt requires reconciliation before another invocation. The producer uses the same distinction for coding and review receipts.

| Work platform | Evaluator |
|---|---|
| Task, private worker, workspace, candidate, repairs | Frozen suite, trial plan, hidden checks, grading |
| Public verification and fresh review evidence | Independent observations and assessments |
| Local deliverable and later authorized publication | Exact-candidate decisions and comparisons |

The producer remains a CLI workflow. Reuse the evaluator's comparison and assessment workbench rather than building a second experiment UI or copying its graders.

## Current source contract

`src/agent_eval/workbench/submissions.py` defines `agent-eval.submission/v1`. The workbench API has a submissions route that requires project run authorization. Intake checks a previously issued execution contract and requires every artifact to be registered in the evaluator's producer-artifact store. Successful intake returns `awaiting_independent_evaluation`, which does not mean acceptance.

Concrete differences from this platform's initial proposal:

| Field or operation | Evaluator source | Producer implication |
|---|---|---|
| Candidate revision | Required hexadecimal revision, 40 to 64 characters | Do not send null or manufacture a Git commit ID. Establish a supported candidate identity with the harness. |
| Artifact storage | Hashes registered JSON objects | Wrap binary patch content in an agreed JSON envelope and retain its independent raw-content hash. Raw artifact keys are not directly interchangeable. |
| Artifact roles | `patch`, `trace`, `tests`, `candidate` | Agree how public verification and independent review evidence are represented. |
| Execution identity | Harness-issued record binds base, candidate, tree, and recipe | Provision and validate that record before submission; a producer cannot self-issue acceptance. |
| Decision | Candidate artifact, comparison/policy/assessment digests, `verdict`, reasons | Bind the authenticated decision to its expected cohort and local candidate mapping. |

The evaluator's decision currently comes from `workbench/analysis.py`; it is not the original planned envelope containing producer execution ID, submission digest, and evaluator revision. Preserve those associations in a bridge record instead of assuming the wire response contains them.

## Local boundary checks

`evaluation.py` uses the distinct schemas `swe-platform.evaluation-proposal/v1` and `swe-platform.decision-binding/v1`. These test artifact substitution, issued identity, candidate binding, policy changes, and evaluator revision changes. They are not advertised as wire-compatible with the current evaluator.

Before connecting the API, pin the evaluator revision, agree the candidate/artifact mappings, authenticate the transport, and run a round-trip against disposable evaluator state. Continue keeping hidden suites, acceptance policy, and independent grading outside worker access. Recheck this document as the evaluator implementation settles.

This investigation only read evaluator source. It did not modify either evaluator checkout, message the other task, run paid evaluations, restart the reviewer watcher, or change the cluster.
