# Implementation and learning path

All commands and module paths in the proposed product sections are targets for implementation, not working software today. Complete milestones in order; expand only after their acceptance gates pass.

## Intended Mac installation and launch

Use Python 3.12+, uv, Git, Docker, and the selected coding CLI. Node 22.19+ is needed for the existing Flue integration. `doctor` should inspect installed versions, Docker availability, adapter capabilities, free disk, and credential availability without exposing values. Check existing `gh` authentication before requesting access. No cloud account is required for local orchestration.

After implementation, the intended flow is:

```sh
cd /Users/chris/Projects/multi-agent-software-engineering-platform
uv sync --locked
uv run swe-platform doctor
uv run swe-platform init
uv run swe-platform serve
```

`init` creates mode-700 state under `~/Library/Application Support/SWEPlatform`, writes non-secret defaults, and never overwrites existing configuration. `serve` uses a Unix socket and displays a clear ready state. A second instance exits safely. Installation never starts the GitHub watcher or submits work. A later optional launchd service uses the same configuration and cleanup rules. The laptop must remain awake for continuous execution; launchd does not make a sleeping Mac available.

Register a repository with an explicit trusted verification recipe, network profile, and Flue executable path. Keep private credentials and endpoints in Keychain or the existing local credential mechanism, outside this project. Do not inherit arbitrary repository YAML as privileged configuration. If setup lacks sandbox capability, explain the failing prerequisite rather than silently falling back to unrestricted execution.

```sh
# Proposed commands once M1 exists
uv run swe-platform repo add /Users/chris/Projects/pr-review-agent-flue
uv run swe-platform submit \
  --repo pr-review-agent-flue \
  --task 'Prevent stale-head review publication; add fake-server race coverage' \
  --mode implement-review --local-only
uv run swe-platform status <job-id>
uv run swe-platform inspect <job-id>
uv run swe-platform cancel <job-id>
uv run swe-platform resume <job-id>
```

Inspect shows the task, current stage, candidate diff, trusted checks, reviewer findings, attempts, remaining budget, and required action. It should distinguish `ready_local`, `needs_attention`, `failed_infra`, and `cancelled` in ordinary language. Unknown cost remains visible. No UI should require understanding lease epochs to decide whether a patch is ready.

After M3, `publish <job-id> --draft` explicitly authorizes the displayed exact candidate and target. This command pushes the dedicated branch and creates a draft PR; it does not merge. No publication commands are run in this planning task.

## Daily-use examples

| Task | Routing and useful output |
|---|---|
| Fix the Flue watcher stale-head race | One writer, fake GitHub transport, trusted TypeScript verification, private Flue review. Local patch first. |
| Add HTTP timeout coverage to agent-eval-k3s | Worker edits a separate candidate checkout; tests and review are tied to that candidate. Its own changed evaluator cannot certify itself for promotion; use a frozen independent evaluator revision. |
| Investigate scheduler duplicate execution | After M4, read-only specialists inspect queue and SQL ownership independently, then one writer synthesizes and patches. Race tests determine correctness. |
| Improve recurring weak handoffs | Across-task analysis proposes a prompt change, tests against training fixtures, then requests independent held-out evaluation. Failed proposals remain recorded. |

The first real Flue task has a small blast radius and direct value: current code fetches a PR head, later fetches a diff, then posts status/review without a final consistency check. Define the desired behavior with fake-server interleavings before choosing the patch. A prepublication head check alone is not atomic; commit binding, postcondition verification, and correct stale outcomes must be considered.

## Milestones with acceptance and learning

### M0: Durable execution skeleton

Deliver Typer CLI, typed job model, SQLite migrations, event/effect transaction, Unix-socket service, scripted worker, labeled execution ownership, and deterministic fixture repository. Implement no live model or GitHub API access.

Acceptance: duplicate submission returns the same job; conflicting duplicate rejects; two service instances cannot both own jobs; a killed coordinator recovers the recorded attempt without a second writer; stale completion is rejected; cancellation is not final until termination is confirmed; a fake external effect reconciles after a lost response. A fixture candidate reaches `ready_local` with inspectable evidence.

Learn: transactions, compare-and-swap, idempotency versus deduplication, state machines, processes versus jobs. Explain `store.transition`, `supervisor.reconcile`, and `effects.reconcile` from input through persistence. Debug: kill the coordinator after intent commit and before dispatch, then after simulated effect completion and before confirmation. Predict the recovered state before running it.

### M1: Real coding adapter and safe execution

Deliver private clones, Docker execution profiles, Codex JSONL adapter, credential broker compatibility spike, trusted baseline/candidate verification recipes, bounded logs, and immutable candidate artifacts. Pin CLI and image versions after the spike. Reject unsupported authentication/isolation combinations.

Acceptance: a harmless real task completes on a disposable fixture; injected repo instructions cannot gain publication tools; code cannot read host canary files, real credentials, Docker socket, evaluator files, or control API; forbidden network destinations fail; cancellation stops a spawned grandchild process; disk/output limits stop runaway work. Tests run on the exact sealed candidate. Credential tests use canaries rather than real secret printing.

Learn: filesystem and network boundaries, subprocess I/O, backpressure, resource limits, content hashing. Explain `adapters/codex`, `workspace/snapshot`, `sandbox/docker`, and `verification/recipes`. Debug: a hanging subprocess, a full output pipe, a Docker daemon outage, and a symlink escape attempt.

### M2: Flue review and bounded repair on a real repo

Deliver one-shot Flue adapter, digest/schema/hunk checks, two-round repair policy, and the Flue stale-head task as a local patch. Reuse the installed reviewer without changing its running watcher. Add a Claude adapter only after the first end-to-end path is stable.

Acceptance: fake-server regression fails on the original code and passes on the candidate; repository verification passes; review and tests bind to final candidate; a blocked zero-exit Flue verdict blocks acceptance; an invalid/oversized review fails closed; each repair invalidates prior evidence; an unresolved blocker ends with an actionable report. Chris can inspect and use the local result without manually reconstructing the session.

Learn: contract validation, time-of-check/time-of-use races, independent context, quality versus infrastructure failures. Explain `review/flue`, `policy/repair`, and candidate evidence invalidation. Debug: supply an old verdict digest, an out-of-hunk finding, a blocked exit-zero response, and a repeated non-improving repair.

### M3: Reviewable PR and operational reliability

Deliver exact-candidate publication authorization, dedicated-branch push, paginated PR reconciliation, restart/sleep handling, retention, backup/restore, and a minimal local inspection UI if the CLI becomes limiting.

Acceptance: an explicitly authorized actual repository task creates one verified draft PR; injected API timeouts do not cause blind duplicate POSTs; a changed branch/base produces stale state; cancellation records any effect already completed; restore recovers jobs and artifacts with matching digests. Run 20 scripted crash scenarios with no accepted stale result and no overlapping accepted writer. Conduct at least one manual Mac sleep/wake drill.

Learn: distributed side effects, outbox patterns, optimistic concurrency, audit and restoration. Explain `github/publisher`, `authorization`, and `retention`. Debug: GitHub accepts a request but response is lost; then simulate the remote branch changing during approval. Explain why exactly-once claims would be misleading.

### M4: Selective delegation and fair measurement

Deliver versioned routing profiles for single agent, independent review, and selective read-only specialists. Add structured question/answer handoffs and shared budget reservations. Delegate only with an independent question, artifact boundary, deadline, and merge rule. Do not introduce shared writable workspaces.

Acceptance: the [comparison protocol](VERIFICATION.md#benchmark-protocol) runs through the independent evaluator; totals include every participant and retry; disagreement and missing specialist evidence are visible. Routing stays single-agent when evidence of benefit is absent. Publish only sanitized aggregate evidence after separate future authorization.

Learn: experiment design, paired comparisons, confidence intervals, cost accounting, context engineering. Explain `routing`, `handoffs`, and `budget.reserve`. Debug: two specialists disagree, one times out, and both try reserving the last available budget concurrently.

### M5: Across-task improvement

Deliver safe trace projection, recurring-failure clustering, versioned candidate proposals, frozen evaluator runs, explicit promotion and rollback records. Start with manual invocation, then optional scheduled analysis once useful. No automatic code/prompt promotion.

Acceptance: a seeded recurring failure generates a supported proposal; a superficially better but held-out-regressing proposal is rejected; hidden tests and thresholds remain inaccessible; baseline and candidate are recoverable; rolling back changes only future job defaults, not history. Demonstrate one independently supported improvement or honestly record no improvement.

Learn: leakage, overfitting, selection bias, drift, versioned experiments, canary rollout. Explain `improvement/proposals` and the external experiment boundary. Debug: propose a change that improves training cases by weakening validation, and show why it cannot pass promotion.

### M6: Team deployment, conditional on real use

Deliver a separate Linux deployment profile, authenticated API, Postgres/object storage, Temporal orchestration, short-lived worker credentials, and dedicated worker isolation. Preserve CLI/local usefulness. Start with one organization before multi-tenancy.

Acceptance: authenticated users cannot retrieve another tenant's job, artifact, trace, or credential; stale remote workers cannot publish; database restoration and worker loss are exercised; workflow version upgrades preserve in-flight jobs; workload and operational targets are written before load testing. No enterprise-ready claim until independent security and operational evidence supports it.

Learn: distributed leases/fencing, OIDC/RBAC, workload identity, workflow replay/versioning, RPO/RTO, SLOs. Explain API authorization to DB/artifact access and a remote job's cancellation path. Debug: partition a worker, rotate its credentials, and restart the control plane while preserving the intended job result.

## First implementation task

**Build a credential-free, restartable local job that produces a fixture patch.**

Scope: scaffold `pyproject.toml`, `src/swe_platform/{cli,models,store,service,supervisor,effects}.py`, schema migration 001, scripted worker fixture, and tests. Create a tiny disposable Git repository with an intentionally failing function and trusted test. The scripted worker changes the fixture, emits a candidate, and has deterministic fault points. Start with one job and one writer. Include execution IDs and epochs even before multiple workers.

Done means `submit`, `status`, `inspect`, `cancel`, and restart recovery work against this fixture; job/effect writes are atomic; invalid transitions and stale epochs reject; no credentials/network are needed; generated artifacts stay outside source; the README gains actual verified commands. Test the important crash boundaries, not just the happy-path reducer. This is the first implementation PR-sized unit. Do not build a dashboard, model router, GitHub publisher, or framework abstraction factory in that unit.

## Portfolio evidence to collect while building

Keep one reproducible demonstration per milestone: a recovery transcript, rejected unsafe candidate, exact-content review, publication reconciliation, paired benchmark, and rejected/promoted improvement. Record measured values and environment versions. Chris should be able to explain the failure that motivated each component, the simpler alternative, and how its test would fail if the protection were removed. Avoid claiming time saved or production scale without measurement.
