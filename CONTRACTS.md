# Contracts

The executable models and adapters are authoritative. Persisted JSON uses canonical serialization and SHA-256 identities. Pydantic models reject extra fields, normalize defaults before hashing, and hide input values in validation errors.

## Fixture service

`models.Submission` v1 contains a submission key, task description, adapter (`scripted` or `docker-scripted`), deadline, fixture delay, and behavior. Task text is descriptive for these deterministic fixtures; repository tasks use `workflow run` or `candidate code`.

SQLite stores jobs, attempts, ordered events, and effects. A duplicate key with identical normalized payload returns the same job. A conflicting payload rejects. Updates compare job version and, where applicable, owner, epoch, and lease. A state transition and its effect intent are one transaction.

The ordinary fixture path is `queued -> implementing -> verifying -> ready_local`. Incorrect fixture output becomes `rejected`. Cancellation persists `cancelling` before confirmed `cancelled`. Missing or unsafe execution evidence produces an attention or infrastructure outcome. A ready result may later become `stale`.

The Unix-socket RPC uses an `ok` envelope around operation results so a job's own `error` field cannot be confused with a transport error. Operations are `submit`, `status`, `inspect`, and `cancel`.

## Candidates and evidence

`development-evidence-bundle/v1` is a bounded portable JSON document containing the exact candidate manifest and artifact bytes, public recipe, verification and review receipts, and typed run metadata. Verification rejects unrelated artifacts, substituted candidate bytes, mismatched checks and reviews, invalid file modes, and inconsistent recipe identities. Its digest is over the exported bytes.

| Record | Binding |
|---|---|
| `candidate/v1` | Base Git revision, files and modes, tree digest, changed paths, binary patch digest |
| `verification-recipe/v1` | Immutable image, fixed argv, deadline |
| Verification receipt | Candidate digest, recipe digest, exit status, reason, output artifact |
| Review receipt | Candidate digest, validated Flue verdict, unknown cost where appropriate |
| Coding receipt | Execution and request digests, scoped output, model request count, usage, image and private-profile digest |
| Repair lineage | Ordered candidate digests and maximum two repairs |

Candidate keys identify immutable content. Workflow journals identify activity around it. Verification receipts are reused only for the saved candidate and recipe. A current passing candidate with a clear review is `ready_local`; missing evidence is `needs_attention`, and a superseded candidate is `stale`.

Candidate intake accepts an existing binary patch or a completed isolated coding result. New and deleted files are represented explicitly. Repair input is a replacement patch against the original base, rather than a patch against the previous candidate.

## Development workflow

`development-workflow/v1` binds the source snapshot, task, allowed paths, recipe, runtime image, coding/review profile identities, absolute deadline, shared request cap, and repair limit to one key. Its journal contains ordered events, named stage intents and results, the active execution, and candidate lineage.

The original key is saved for control-panel operations. A short dispatch lock serializes stage admission with cancellation. Resume restores the active stage before continuing its operation. Cancellation is confirmed only after active execution termination; no active coordinator is required to cancel an inactive job.

## GitHub publication

`github-publication-plan/v1` pins repository and actor identities, base and new branch, locally computed Git tree/commit, candidate and evidence digests, PR text and a 24-hour expiration. Its canonical digest is the approval token. A durable publication journal records branch and PR intents, receipts, reconciliation events and checks tied to the remote head.

POST `/api/workflows/{id}/publications` prepares a plan. POST `.../{plan}/publish` requires the same `plan_sha256` in its body. POST `.../{plan}/reconcile` performs remote reads only. All mutations require an operator token; viewers may inspect evidence and download bundles.

Model-request reservations are committed before stage dispatch. A completed receipt replaces its reservation with the observed request count. An uncertain attempt keeps the reservation. Reusing the key with a changed policy rejects; resuming the same job cannot reset the budget or deadline.

Coding and review use separate Flue agent executions. Verification is deterministic. Passing checks and a validated clear review establish `ready_local`. Failed checks or a blocking review can lead to a bounded repair. Cancellation prevents new stages and requires confirmation that active execution stopped. Interrupted dispatch without a receipt requires reconciliation.

## Agent broker

A request frame contains exactly `type`, `id`, `path`, and a base64 body. The only model path is `/v1/chat/completions`. IDs must be unique within the attempt. The host pins the configured model and charges the request allowance before dispatch. Coding permits only Flue's read, write, edit, bash, grep, and glob tools; review permits no tools. Both outgoing tool definitions and incoming tool calls are checked. Unknown capabilities, transport fields, and remote media reject.

Replies carry an ID, HTTP status, content type, and bounded base64 data. A worker emits a startup handshake and a terminal envelope. The `flue-result/v1` collector requires the expected role, submission identity, completed status, nonempty final message, valid usage, and successful process exit. Incomplete or malformed output rejects. Dollar cost remains null when unreported.

An attempt with intent but no durable receipt is ambiguous and is never automatically replayed. `candidate stop-coding` or `workflow cancel` terminates its container. A completed receipt can finish candidate intake without another model call. Runtime and image identity are part of the request; changing runtimes does not silently reinterpret an old execution key.

## Review evidence

The built-in Flue reviewer consumes the complete exact diff and its digest in a fresh conversation. Its v1 verdict contains `input_sha256`, `risk`, `blocked`, findings, and rationale. Findings must point to changed lines. Malformed, oversized, inconsistent, or blocked output cannot establish a clear review.

Standalone review receipts bind the selected runtime, image, gateway identity, and budget. Workflow review belongs to that job's isolated evidence namespace. A legacy external raw-diff adapter remains available internally for historical integration fixtures; it is not required by the current workflow or CLI.

## Evaluation and publication

`swe-platform.evaluation-proposal/v1` and `swe-platform.decision-binding/v1` are local evidence-binding fixtures. Validation binds issued execution identity, artifacts, candidate, policy, and evaluator revision. These intentionally use separate names from the evaluator's evolving wire schemas. A future bridge must authenticate origin and translate the differences recorded in [INTEGRATION.md](INTEGRATION.md), including candidate revisions and registered JSON artifact storage.

`publication-plan/v1` binds repository, branch, base/head revisions, candidate digest, title, and body. Simulation authorization binds the exact plan digest and expiry. Only the offline fake remote is accepted. Lost responses reconcile by exact target, author, and marker across pages; an uncertain absent effect is not blindly repeated.
