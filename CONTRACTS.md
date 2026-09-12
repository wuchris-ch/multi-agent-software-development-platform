# Contracts

The executable models and adapters are authoritative. Persisted JSON uses canonical serialization and SHA-256 identities. Pydantic models reject extra fields, normalize defaults before hashing, and hide input values in validation errors.

## Fixture service

`models.Submission` v1 contains a submission key, task description, adapter (`scripted` or `docker-scripted`), deadline, fixture delay, and behavior. Task text is descriptive for these deterministic fixtures; arbitrary repository coding uses `candidate code`.

SQLite stores jobs, attempts, ordered events, and effects. A duplicate key with identical normalized payload returns the same job. A conflicting payload rejects. Updates compare job version and, where applicable, owner, epoch, and lease. A state transition and its effect intent are one transaction.

The ordinary fixture path is `queued -> implementing -> verifying -> ready_local`. Incorrect fixture output becomes `rejected`. Cancellation persists `cancelling` before confirmed `cancelled`. Missing or unsafe execution evidence produces an attention or infrastructure outcome. A ready result may later become `stale`.

The Unix-socket RPC uses an `ok` envelope around operation results so a job's own `error` field cannot be confused with a transport error. Operations are `submit`, `status`, `inspect`, and `cancel`.

## Candidates and evidence

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

## Coding broker

A request frame contains exactly `type`, `id`, `path`, and a base64 body. The only path is `/v1/responses`. IDs must be unique within the attempt. The host replaces the requested model with trusted configuration and charges the request allowance before dispatch. Hosted tools, arbitrary transport fields, remote media, and cross-request response handles reject.

Responses carry an ID, status, content type, and bounded base64 data. The Codex JSONL collector requires a successful exit, a final agent message, and a completed turn. Truncated streams, explicit errors, malformed usage, and events after completion reject. Dollar cost remains null when unreported.

An attempt with intent but no durable receipt is ambiguous. It is never automatically replayed. `candidate stop-coding` terminates its container; an intentional new invocation uses a new key. A durable receipt can complete candidate intake without another model call.

## Flue

The existing raw-diff CLI consumes exact diff bytes on stdin. Its v1 verdict includes `input_sha256`, `risk`, `blocked`, findings, and rationale. The adapter verifies the input digest and finding locations against changed lines. Malformed, oversized, inconsistent, or blocked output cannot establish a clear review.

The trusted Flue process receives only its model environment keys and a fresh home. Credential values, raw provider diagnostics, and worker prompts are not included in candidate review receipts.

## Evaluation and publication

`swe-platform.evaluation-proposal/v1` and `swe-platform.decision-binding/v1` are local evidence-binding fixtures. Validation binds issued execution identity, artifacts, candidate, policy, and evaluator revision. These intentionally use separate names from the evaluator's evolving wire schemas. A future bridge must authenticate origin and translate the differences recorded in [INTEGRATION.md](INTEGRATION.md), including candidate revisions and registered JSON artifact storage.

`publication-plan/v1` binds repository, branch, base/head revisions, candidate digest, title, and body. Simulation authorization binds the exact plan digest and expiry. Only the offline fake remote is accepted. Lost responses reconcile by exact target, author, and marker across pages; an uncertain absent effect is not blindly repeated.
