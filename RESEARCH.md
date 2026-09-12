# Research and design decisions

Initial research was conducted September 11, 2026. Source inspection informed the design; the [verification record](VERIFICATION.md) records executed tests. Research reports and vendor documentation are references, not proof that this implementation has the same behavior.

## Existing systems

| System | Inspected baseline | Integration decision |
|---|---|---|
| Flue runtime | 2.0.3, Pi 0.83.0; installed source and workflow/agent APIs | Use Flue for coding and review, with role-specific capabilities and deterministic cross-stage orchestration. |
| Agent evaluator | `9b68ab520427e93a104a8d36966401a499f2fd92`, plus its active implementation | Keep transport, experiment scheduling, hidden checks, and grading independent. Recheck changing contracts. |
| Existing Flue reviewer | `024f477e613de41a23a4b6a8986c735f703bbdae` | Preserve its verdict schema and keep the running watcher untouched. |

The Flue wrapper already validates bounded JSON verdicts, handles partition/format attempts, and runs fresh review contexts. A blocked verdict can exit zero. The platform therefore validates `blocked`, digest, schema, and line membership independently. The current workflow implements its own Flue review role using the same strict evidence contract and does not introduce another watcher.

The evaluator already supports black-box CLI/HTTP targets and specialized review/coding assessment. Its active changes add durable trials, a workbench, and distributed execution. The producer emits candidates and public evidence, while acceptance remains independent. The current source differences are recorded in [INTEGRATION.md](INTEGRATION.md).

## Research references

The decisions below are design inferences from the cited sources, subsequently tested where the verification record says so.

| Reference | Design implication |
|---|---|
| [Cognition: Multi-Agents, What's Actually Working](https://cognition.com/blog/multi-agents-working) | Start with independent review and deliberate feedback to one writer. Measure whether additional roles help. |
| [Google Research: When and why agent systems work](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/) | Coordination benefits depend on task structure. Evaluate routing on representative tasks rather than equating agent count with quality. |
| [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | Separate tasks, trials, observations, and graders. Preserve first attempts and distinguish final-state evidence from agent claims. |
| [Anthropic: Infrastructure noise](https://www.anthropic.com/engineering/infrastructure-noise) | Pin resource allocation and execution profiles in comparisons; retain infrastructure failures and missing observations. |
| [LangSmith Engine architecture](https://www.langchain.com/blog/how-we-built-langsmith-engine-our-agent-for-improving-agents) | Trace-based diagnosis should produce inspectable proposals and separate verification. Suggested graders remain proposals. |
| [OpenTelemetry Gen AI attributes](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/) | Keep canonical local evidence independent of any eventual telemetry projection. |

## Component choices

| Choice | Reason and revisit condition |
|---|---|
| Flue plus a deterministic coordinator | Flue owns agent conversations and tool execution. The coordinator owns budgets, exact evidence, and durable stage transitions. |
| Python, Pydantic, Typer | Preserve the verified snapshot, container, artifact, and evaluation interfaces as ordinary services. |
| SQLite and an explicit supervisor | Local transactions can bind state, events, and effects. Revisit distributed orchestration when remote ownership becomes a requirement. |
| [Temporal](https://docs.temporal.io/activity-execution) as a later distributed option | Durable scheduling is useful for remote execution; application idempotency and process termination still need explicit handling. |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) as an alternative | Reconsider when dynamic graph composition earns its complexity. A graph checkpoint does not itself reconcile an external effect. |
| [OpenHands SDK](https://github.com/OpenHands/software-agent-sdk) and [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) as worker references | Keep adapters replaceable and preserve a strong simple baseline instead of rebuilding every agent loop. |
| [Docker isolation](https://docs.docker.com/engine/security/) | Practical local execution with constrained mounts and resources. Verify enforcement with actual canaries. Evaluate stronger Linux isolation for shared hostile workloads. |

## Decisions revised during implementation

The initial coding-CLI integration was replaced with Flue agents for both coding and review. A registered provider connects them to the configured model gateway. This removes the extra agent runtime and makes role policy explicit. Flue's [workflow guide](https://flueframework.com/docs/guide/workflows/) distinguishes durable individual submissions from the script around them; the coordinator checkpoints stage intents and receipts for that cross-stage boundary.

Content-only snapshots replaced full clones so workers receive no source repository history or local Git configuration. Candidate acceptance binds a content digest even before a Git commit exists.

The model transport uses attached container pipes, allowing worker networking to remain disabled. Actual CLI probes exposed enabled hosted tools and a bridge shutdown failure; these were corrected before the live coding exercise. A concurrent-input/output regression ensures trusted transport input travels through an anonymous pipe without temporary-file storage.

Candidate stages use durable receipts, with explicit attention for dispatch without a receipt. This is more useful than promising to replay a model conversation after an uncertain external call.

The evaluator's implementation diverged from its initial proposed wire contract. Local evidence-binding fixtures now have separate schema names, and the integration plan specifies the required translation rather than assuming compatibility.
