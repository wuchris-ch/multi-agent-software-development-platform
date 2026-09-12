# Design decisions

## One authority for each kind of state

The workflow coordinator owns stage transitions, the candidate workbench owns exact evidence, and the publisher owns remote effects. Agents return proposals and results. The console calls those owners and builds a read model from their journals. This keeps CLI and UI behavior consistent and avoids a second queue that could disagree with execution state.

## Durable local execution before a distributed service

Repository workflows use exclusive per-key ownership and atomic file journals. That matches a local operator deployment and makes recovery reproducible without additional infrastructure. The separate SQLite supervisor fixtures exercise transactions, leases and stale epochs. A distributed scheduler would require a shared transactional owner and execution fencing before multiple hosts could safely claim the same job.

## Separate production evidence from acceptance

The coding workspace, fresh public verifier, review conversation and independent evaluator have distinct inputs and capabilities. Candidate content and evidence are immutable and addressed by digest. Editing a patch invalidates its prior checks and review. A receipt proves which operation was observed; it does not grant authority to accept a candidate.

## Treat an uncertain write as an observation problem

Publication writes intent before contacting GitHub. A known commit identity and a unique new branch make content creation recoverable. PR creation has no native idempotency key, so a dispatched POST without a receipt is reconciled by reading and matching its exact identity. An unmatched uncertain POST remains unresolved instead of creating a duplicate.

## Bound work before dispatch

One absolute deadline and shared request budget cover coding, independent review and repairs. Reservations survive interruption. Cancellation and dispatch share a short admission lock so cancellation cannot race into a new stage. The full coordinator lock keeps one writer accountable for candidate lineage.

## Failure behavior

| Failure boundary | Preserved state | Recovery |
|---|---|---|
| Coordinator stops after agent receipt | Agent result and reserved stage | Resume uses the result and seals it once. |
| Model dispatch has no receipt | Execution intent and allowance | Keep it unresolved; do not reset the allowance. |
| Cancellation arrives before launch | Durable tombstone | Reject admission and confirm inactive cancellation. |
| Candidate changes after verification | Old evidence and new candidate digest | Require checks and review for the new content. |
| GitHub creates PR but process loses response | PR intent and exact plan | Read-only reconciliation adopts one matching draft. |
| Remote base or branch differs | Exact expected identities | Stop publication and prepare a reviewed replacement plan. |
| One console job is corrupt | Other journals remain independent | List healthy runs and show the unavailable count. |
| Exported artifact is replaced | Original expected bundle/artifact digest | Offline verification rejects substitution. |

Each boundary has focused tests. The GitHub response-loss case was also exercised against a real disposable repository with a process exit between remote creation and local receipt.
