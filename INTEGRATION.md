# Evaluator integration boundary

Inspected September 11, 2026 during implementation. The evaluator's active work is in
`/Users/chris/.codex/worktrees/dd44/agent-eval-k3s`, based on
`9b68ab520427e93a104a8d36966401a499f2fd92`. Its original checkout is not the current
implementation worktree. Neither tree is modified by this project.

The evaluator is extending its existing black-box transports and graders with a
durable experiment journal. At inspection, new experiment models and a scoring
extraction were in progress. Its proposed later API is not an available service.
The critical recovery distinction is shared: a saved observation can be graded
again, while dispatch without a receipt requires reconciliation before another
invocation with possible side effects.

Responsibilities remain separate:

| Work platform | Evaluator |
|---|---|
| Task, worker, private workspace, candidate, repairs | Frozen suite, trial plan, hidden checks, grading |
| Public verification and independent review evidence | Independent observation and assessment |
| Local deliverable and later authorized publication | Exact-candidate decision and comparative experiments |

Use the proposed `agent-eval.submission/v1` format from the evaluator's
`docs/platform-plan/architecture.md` as a **contract fixture**, not a claim that
its submission API exists. The harness supplies an opaque execution ID. The
producer supplies its run ID, base/candidate revision, content digest, recipe
digest, approved artifact storage keys and explicitly unknown usage where needed.
The platform never imports evaluator internals or opens a hidden suite to adapt
its current answer. Producer completion and a Flue verdict cannot certify a
benchmark win. Recheck source and contract before connecting the actual API.

No peer-task messages, changes to evaluator worktrees, paid evaluator runs,
watcher restarts, or cluster changes were made by this integration investigation.
