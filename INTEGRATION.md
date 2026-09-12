# Independent evaluation

The producer and evaluator have separate authority. The producer creates candidate bytes and execution evidence. The evaluator reserves trials, reconstructs patches against its own base snapshot, runs independent behavior checks and issues bound assessments.

The v2 schemas are pinned to [agent-eval-platform revision e028f37](https://github.com/wuchris-ch/agent-eval-platform/tree/e028f37195cbd8519b80d7b2e20af443ec57502f/contracts/v2). The shipped schema manifest records each file hash. Runtime connections also pin the evaluator's content identity through its authenticated `/v1/authority` endpoint.

## Trial lifecycle

1. The evaluator operator registers a base revision, production recipe, acceptance policy and suite, then reserves a trial ticket before production.
2. `producer run` verifies the ticket, model configuration, capabilities, policy and budget before dispatching the workflow. A ticket binds a cohort, task family, split, paired trial and arm. Failed production remains a reserved attempt.
3. `producer upload` stores the sealed candidate, binary patch, trace and available public verification/review as registered artifact envelopes. It returns the candidate binding requested from the evaluator.
4. The evaluator operator verifies the patch against independently captured base bytes and issues one immutable candidate-bound execution contract for the ticket.
5. `producer submit` validates that contract and sends an immutable submission. Intake returns `awaiting_independent_evaluation`.
6. The evaluator runs its independent checks. `producer assessment` verifies the authenticated assessment against every submitted identity before recording acceptance.

```sh
uv run swe-platform producer recipe request.json
uv run swe-platform producer run request.json ticket.json evaluator.json
uv run swe-platform producer upload <workflow-key> evaluator.json
uv run swe-platform producer binding <workflow-key>
# The evaluator operator issues the candidate contract.
uv run swe-platform producer submit <workflow-key> evaluator.json
# The evaluator operator runs the reserved independent suite.
uv run swe-platform producer assessment <workflow-key> evaluator.json
```

Gateway and evaluator profiles are private host configuration. The evaluator profile specifies an origin, project, expected implementation SHA-256 and the environment variable containing its runner token. The client uses bearer authentication and `X-Project`, rejects redirects, bounds responses and permits only producer operations. It cannot issue trial tickets, execution contracts or grading authority.

## Content and usage bindings

| Identity | Binding |
|---|---|
| Candidate | Real base revision, nullable candidate Git revision, exact tree and canonical manifest hashes |
| Artifact | Raw bytes SHA-256 and canonical base64 envelope storage key |
| Execution | Reserved ticket, task/trial/arm, recipe, suite, acceptance policy and evaluator identity |
| Production recipe | Producer implementation and policy, capabilities, model configuration and shared resource ceilings |
| Assessment | Exact submission, execution contract, candidate, independent observation and checks |

Model usage is observed by the producer's host broker and remains labeled `producer_reported` at the evaluator boundary. Unknown usage and monetary cost remain null. Independent behavior acceptance does not manufacture resource observations. Public checks, review and independent acceptance are distinct evidence.

A reserved evaluation must accept the current candidate before its workflow can publish a draft PR. Publication approval incorporates the independent assessment digest. A changed or superseded candidate invalidates that evidence.

## Verified protocol control

On September 11, 2026, a pinned producer and a separate pinned evaluator completed an authenticated disposable round trip without model calls. The producer admitted evaluator-issued tickets, sealed real Git-backed candidates, ran public verification and uploaded exact artifacts. The operator independently issued contracts and graded behavior and final durable state.

| Candidate | Public verification | Independent checks | Producer result |
|---|---|---|---|
| Correct order handling | Passed | 5/5 passed | Accepted |
| Plausible duplicate-order handling | Passed | 1/5 passed | Failed |

Repeated authenticated assessment retrieval preserved the same identities and result. The real integration also established the required ordering: artifact upload precedes candidate-contract issuance. Executable tests cover this ordering, lost intake receipts, schema drift, forbidden producer operations and substituted candidate, policy, ticket, evaluator and submission identities.

## Paired evaluation and policy rollout

Production policies are compared within matched task pairs using the same model configuration, tool capabilities and total budgets. Different task families can have different public recipes and write scopes. A frozen promotion gate enumerates every task, repetition and arm; missing or duplicate attempts cannot disappear from its denominator.

Development comparisons must pass before held-out task families begin. The held-out families are disjoint from development. The policy miner reads development traces only and cannot use hidden tests or alter the evaluator's acceptance policy. Promotion requires complete paired evidence, the declared development gain and no passing-baseline regression in either split. Rollout and rollback compare both policy identity and generation before updating the active pointer.
