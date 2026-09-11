# Multi-agent software engineering platform

Implementation plan, researched September 11, 2026. **No application is implemented in this folder.** The directory did not exist when inspected and was created for these documents. Existing projects were inspected read-only. No commits, pushes, deployments, or external messages were made.

Build a local service that takes a repository task, gives an existing coding tool an isolated workspace, verifies its patch, obtains an independent Flue review, allows bounded repairs, and prepares a reviewable pull request. The useful product is reliable execution and inspectable evidence. Additional agents earn their place through measured improvements.

## Recommendation

Start with **Python, Pydantic, Typer, SQLite, a small explicit state machine, and Docker-isolated workspaces**. Use Codex CLI as the initial coding adapter, then Claude Code. Call the existing Flue diff reviewer through its one-shot JSON interface. Keep agent-eval-k3s as the independent evaluation authority. Add read-only specialist delegation after the single-writer workflow works reliably. Revisit Temporal when remote workers or high availability become real requirements; do not combine it with LangGraph in the first release.

This is an ambitious path to a useful personal platform and later a team service. It is not evidence of enterprise readiness. The hardest and most valuable work is recovery, verification independence, safe credentials, and preventing duplicate effects.

## Read these documents

| Document | What it answers |
|---|---|
| [Research and current evidence](RESEARCH.md) | What exists today, what primary sources support, framework comparisons, and uncertainty |
| [Architecture and trust boundaries](ARCHITECTURE.md) | Components, workflow, recovery, isolation, deployment, and design decisions |
| [State and integration contracts](CONTRACTS.md) | Persistent records, handoffs, coding adapters, Flue, evaluator, GitHub, and observability |
| [Implementation and learning path](IMPLEMENTATION.md) | Mac experience, daily examples, milestones, acceptance criteria, and the first task |
| [Verification and improvement](VERIFICATION.md) | Fault tests, fair benchmarks, independent grading, and controlled improvement |

## First useful outcome

On a clean snapshot of `pr-review-agent-flue`, ask the platform to prevent stale-head review publication. It should reproduce the race against a fake GitHub server, propose and implement a bounded patch in an isolated workspace, run verification, obtain a fresh Flue verdict, and produce a local patch plus evidence. Later, explicitly authorize pushing its dedicated branch and creating a draft PR. This planning task does not change that repository.

The first implementation task is smaller: [build the credential-free durable execution skeleton](IMPLEMENTATION.md#first-implementation-task). Prove that killing and restarting the coordinator does not lose a job or launch a second writer. Do that before spending model tokens.

## What success will look like

Chris uses it on actual repositories, can inspect why a job stopped, resumes after a crash without overlapping writers, and reviews a patch with tests tied to its exact content. A published benchmark then establishes whether independent review and selective delegation improve accepted results per budget. A good interview demonstration is a crash and recovery, an intentionally rejected patch, and a measured comparison, not a screen full of agents talking.
