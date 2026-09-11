# Research and current evidence

Checked September 11, 2026. Labels matter: **observed** means directly inspected code or runtime output; **reported** means a document or prior task says it happened; **proposed** means a design in this plan. Source inspection is not an execution test. No new paid model runs or live publication tests were performed.

## Current local baseline

| Project | Revision inspected | Evidence and implication |
|---|---|---|
| pr-review-agent-flue | `024f477e613de41a23a4b6a8986c735f703bbdae` | Clean checkout. Flue 2.0.3 and Pi 0.83.0 pinned. This is the active reviewer, not a prospective replacement. |
| pr-review-agent | `bf282b154eb7951e8fb895f85fe49e9c9b508ce5` | Clean checkout, README marks it retired. Preserve its contract, not its deployment. |
| agent-eval-k3s | `9b68ab520427e93a104a8d36966401a499f2fd92` | Existing untracked Finder metadata preserved. Current scope includes CLI/HTTP/database black-box evaluation, optional source/trace inspection, and specialized coding/reviewer evaluation. |

Observed runtime: Kubernetes Deployment `agent-eval/pr-review-worker` had one ready replica, `Recreate` strategy, command `node dist/watcher.js`, and image `pr-review-agent-flue:024f477e613de41a23a4b6a8986c735f703bbdae`. The `reviewer-evaluation` CronJob image includes both revisions above. Its checked-in schedule is `0 2 * * *` with `America/Vancouver`. These observations establish the current configured and ready worker, not correctness of every review or completion of today's scheduled benchmark.

The recent task **Migrate PR reviews to Flue** (`01a0921c-64a4-7fb2-8dd1-3406363ba214`) reports 68 passing tests, live GitHub publication, restart deduplication, and a final 20/20 benchmark with zero infrastructure errors. That is historical task evidence, not a test rerun here. The evaluator README's September 3 result is an older 60-evaluation cohort; do not present it as the new Flue release's three-trial result. The parent task **Review resume advice and agents** supplies the portfolio motivation; architecture choices below come from current code and sources.

### How Flue actually runs

Read [runtime.ts](/Users/chris/Projects/pr-review-agent-flue/src/agents/runtime.ts), [runner.ts](/Users/chris/Projects/pr-review-agent-flue/src/runner.ts), [schema.ts](/Users/chris/Projects/pr-review-agent-flue/src/schema.ts), [watcher.ts](/Users/chris/Projects/pr-review-agent-flue/src/watcher.ts), and the [current README](/Users/chris/Projects/pr-review-agent-flue/README.md).

Each partition/format attempt starts a Flue runtime, dispatches one review, reads a bounded reply, and stops. The repository documents in-memory SQLite, and the runtime call supplies no persistent storage configuration. There is no cross-job recovery. The wrapper owns partitioning, validation, child isolation, and bounded correction. The child is not an OS sandbox, receives no GitHub token, and has no repository execution tools. A valid blocked verdict exits zero; callers must inspect `blocked`.

The watcher polls sequentially and retains the old `PR review agent` status and policy-2 completion marker. Inspection confirms only the first 100 PRs/reviews are requested, no final head recheck, and publication without `commit_id`. Marker lookup and posting are separate API operations. One replica and Recreate reduce overlap; they do not establish distributed exactly-once effects. Use this existing service unchanged while integrating the raw-diff CLI. Never start a second watcher as part of platform installation.

The active stack is operated through [review-stack](/Users/chris/Projects/agent-eval-k3s/review-stack), which builds the sibling Flue reviewer. Mac sleep interrupts availability. A ready local Kubernetes service is not an always-available cloud service.

### Evaluation capabilities worth retaining

[agents/base.py](/Users/chris/Projects/agent-eval-k3s/src/agent_eval/agents/base.py) defines executable adapters; [codex.py](/Users/chris/Projects/agent-eval-k3s/src/agent_eval/agents/codex.py) parses JSONL and leaves unpriced subscription cost unknown. Its current execution command bypasses CLI sandboxing inside a pod. Do not copy that command onto the Mac. [blackbox/targets.py](/Users/chris/Projects/agent-eval-k3s/src/agent_eval/blackbox/targets.py) defines bounded CLI/HTTP transports and validated response decoding. [runner.py](/Users/chris/Projects/agent-eval-k3s/src/agent_eval/runner.py) and the isolated task mode remain the place for trusted evaluation. Target code must not receive the evaluator image or scoring authority.

## Lessons from adjacent projects

These are design references, not dependencies to import wholesale. Several contain existing uncommitted work, which was preserved.

| Project and inspected surface | Reuse | Do not assume |
|---|---|---|
| distributed-task-scheduler, `internal/store/postgres.go`, `internal/queue/redis_queue.go`, revision `71af5c7` | Unique submission keys, explicit leases, retry vocabulary | `ExtendLease` and `Ack` take job IDs, not fencing epochs. A lease does not stop a stale worker. Avoid adding Redis plus SQL dual writes here. |
| nexus-mcp-gateway, `gateway/src/router.py`, README, revision `20815a1` with local changes | Tool routing, per-identity authorization and audit concepts | Gateway branding or OAuth alone does not sandbox shell execution. No MCP gateway is required for the first two adapters. |
| agent-gauntlet, `src/graders/index.ts`, `src/core/trace.ts`, revision `c020256` | Grade final state and prohibited actions; stable failure taxonomy | A simulated world benchmark proves behavior only in its tested world. |
| deepswe-claude-code-eval, `harness/scoring.py`, README | Separate hidden checks and unchanged-file constraints; useful fixture ideas | Keeping tests outside a copied directory is not sufficient adversarial isolation when processes can access the surrounding filesystem. |
| agent-reliability-lab, `src/order_agent_lab/agent.py`, README | Render conclusions from typed evidence; fail closed on missing evidence; scripted fault tests | Its fake identity is not production authentication. |
| codetrace, `lib/revision-evidence.ts`, README | Invalidate evidence when the underlying facts change; apply the same rule to patch hashes | Compliance-domain code and private source material are not relevant to copy. |

## Primary research synthesis

The following implications are design inferences, not independently reproduced results.

| Source | Finding or reported practice | Decision for this platform |
|---|---|---|
| [Cognition, Multi-Agents: What's Actually Working](https://cognition.com/blog/multi-agents-working), checked September 11 | Describes clean-context code review and deliberate communication back to the author. | Start with an independent reviewer, bounded feedback, and a coordinator that checks scope. Do not assume different sessions eliminate correlated model errors. |
| [Google Research, scaling agent systems](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/), January 28, 2026 | Controlled experiments across 180 configurations show task-dependent gains and sequential-task penalties. The studied domains are not a direct benchmark of Chris's repositories. | Measure decomposition value. Default to one writer; delegate independent investigation only when there is a clear join condition. |
| [Anthropic, demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents), January 9, 2026 | Explains task/trial/grader distinctions and combining evaluation methods. | Preserve every attempt, use deterministic outcome checks, and separate capability from reliability. |
| [Anthropic, infrastructure noise](https://www.anthropic.com/engineering/infrastructure-noise), February 5, 2026 | Reports material benchmark changes from resource allocation and enforcement, including a six-point Terminal-Bench spread. | Pin CPU, RAM, timeouts, runtime, and enforcement across variants. Track infra errors separately and in overall success denominators. |
| [LangSmith Engine architecture](https://www.langchain.com/blog/how-we-built-langsmith-engine-our-agent-for-improving-agents), May 19, 2026 | Describes trace-based issue discovery, supporting evidence, and testing proposed evaluators. | Separate diagnosis from fixes and promotion. Trace-derived evaluator suggestions remain untrusted candidates. |
| [LangSmith Engine update](https://www.langchain.com/blog/new-in-langsmith-engine-2x-better-issue-detection), August 25, 2026 | Reports improved issue detection on internal benchmarks. Automated verification appears in its future direction section. | Treat the performance numbers as vendor reports, not reproducible proof or a reason to buy a service. Build independent verification explicitly. |

## Framework and component comparison

| Candidate | Evidence inspected | Fit and choice |
|---|---|---|
| Explicit Python state machine + SQLite | Existing Python evaluator interfaces; application design proposed here | **Select for local v1.** A bounded workflow around external processes needs durable stage/result records more than model-level graph state. One DB transaction can update state, append an event, and enqueue an effect. Risk: owning recovery code. Mitigate with narrow scope and mandatory fault tests. |
| LangGraph | [Persistence documentation](https://docs.langchain.com/oss/python/langgraph/persistence); [SQLite saver source](https://github.com/langchain-ai/langgraph/blob/e539ac122f4126f6dd850581c1494948cf620e31/libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/__init__.py); [retry source](https://github.com/langchain-ai/langgraph/blob/e539ac122f4126f6dd850581c1494948cf620e31/libs/langgraph/langgraph/pregel/_retry.py) | Checkpointers store graph state; SQLite implementation has checkpoint/write tables and locked cursor transactions. Retry implementation guards late graph writes. Neither inspected component is a GitHub effect ledger or OS process supervisor. Good candidate if dynamic graph composition becomes a product requirement; defer now. |
| Temporal | [Activity semantics](https://docs.temporal.io/activity-execution); [TypeScript source](https://github.com/temporalio/sdk-typescript/blob/e37ed88b7c71dc35c022464d095bca69a9b2dcd3/packages/workflow/src/workflow.ts) | Source schedules activities with retry/heartbeat/timeouts and cancellation commands. **Preferred distributed evolution.** A service is additional local infrastructure. Retries/cancellation still require idempotent effects and worker cleanup. No runtime resilience tests were run here. |
| Flue | Installed runtime 2.0.3 and actual reviewer integration above | **Reuse behind the reviewer boundary.** Its existing stateless usage cannot provide platform durability. Do not extend it into a second workflow authority. |
| OpenHands SDK | [Repository](https://github.com/OpenHands/software-agent-sdk); [local conversation source](https://github.com/OpenHands/software-agent-sdk/blob/57f5cc9f4a671fe290783551ba00d57efe2017c9/openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py) | Broader agent/conversation abstraction worth considering as an alternate worker. Inspected pause implementation takes effect between iterations, not immediately during a model request. Compare integration effort when remote workspace control is needed; do not rebuild its full agent loop. |
| mini-swe-agent | [Agent source](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py); [Docker environment](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/environments/docker.py) | Strong transparent baseline option. Source checks steps/cost/time before model calls and executes commands via `docker exec`. A host subprocess timeout alone does not prove the process inside the container stopped. Use platform supervision and fixed limits. |
| Codex CLI, Claude Code / Agent SDK | Local CLI help: Codex `0.153.4`, Claude Code `2.1.268`; [Codex noninteractive docs](https://learn.chatgpt.com/docs/non-interactive-mode); [Claude SDK docs](https://code.claude.com/docs/en/agent-sdk/overview) | **Codex CLI first**, because it is installed and its JSONL interface is already understood locally. Claude adapter next. Use explicit pinned versions and capability tests, not assumptions of equivalent flags. Auth methods differ; a personal login is not a shared service credential. |
| Docker, then stronger remote sandboxes | [Docker security](https://docs.docker.com/engine/security/); [gVisor architecture](https://gvisor.dev/docs/) | Docker is practical for personal execution with constrained mounts/network. gVisor adds an application-kernel boundary with compatibility tradeoffs. Evaluate gVisor or microVMs on Linux for hostile multi-tenant work; do not claim native macOS support from Linux documentation. |
| OpenTelemetry and existing Phoenix | [Gen AI attribute registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/), version 1.44.0 page checked | Export a small stable application projection; adapt changing semantic conventions at one boundary. Local DB and artifacts remain authoritative when telemetry fails. |

Open-source URLs above pin the source revisions fetched for inspection, not recommended dependency release pins. Recheck licenses, supported releases, dependency security, and compatibility before implementation. No source was copied into this project.

## Remaining uncertainties and how to resolve them

1. Coding-agent authentication inside the proposed sandbox/proxy is not tested. M1 must demonstrate a no-secret-leak path with the actual selected provider. Do not mount Chris's full CLI home into untrusted containers.
2. SQLite and the supervisor have not been implemented or crash-tested. If recovery invariants become hard to maintain, evaluate Temporal before broadening the local runtime.
3. No benchmark proves delegation improves these repositories. M4 makes that an experiment, with single-agent fallback.
4. The Flue watcher limitations are inspected code behavior. The proposed stale-head task must test races, not rely on a prompt or README update.
5. Model identifiers, pricing, provider quotas, and enterprise deployment choices remain runtime configuration. Missing cost data is unknown, never free.
