# Verification record

Observed September 11, 2026 with Flue runtime 2.0.3, Node 22.22.3, Python 3.12.11, and Docker Engine 20.10.16 on macOS. GitHub CI independently exercised the platform on Linux.

## Automated checks

[CI at `04ef086`](https://github.com/wuchris-ch/multi-agent-software-development-platform/actions/runs/34664403099) passed **90 Python tests in 67.61 seconds**, the Node provider test, lint, formatting, package build, and an installed-wheel Docker smoke test. The installed package completed its fixture as `ready_local` outside the source checkout.

```sh
npm ci --ignore-scripts
npm test
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv build
```

The Docker integration tests run the actual Flue runtime against controlled model responses. They exercise tool execution, review, repair, cancellation, and recovery without paid model requests or GitHub writes.

| Area | Observed assertion |
|---|---|
| Durable state | Atomic rollback, idempotent admission, conflicting payload rejection, stale ownership rejection |
| Coordinator recovery | Crash boundaries, process death, duplicate launch, competing service instances, cancellation |
| Docker | Filesystem and environment canaries, blocked external network, non-root execution, scope enforcement, resource and output deadlines, descendant termination |
| Flue agents | Actual coding and review agents, declared tool capabilities, streamed tool-name validation, strict role-bound results |
| Model broker | Credential exclusion, shared request limits, startup handshake, bounded I/O, redacted failure records, portable credential configuration |
| Development workflow | Code, seal, fresh verification, independent review, bounded repair, stale candidate rejection, stable completed resume |
| Interrupted work | Completed agent receipt finishes candidate intake without another model call; uncertain dispatch is retained for attention |
| Review reuse | Exact candidate and policy binding, receipt reuse, rejection of conflicting image or budget settings |
| Integration contracts | Candidate and decision substitution rejection, expired authorization, paginated fake-remote reconciliation, lost-response ambiguity |

Local Flue agent image used for the live exercise: `sha256:73fd204f5bf3653bd80ab896c470f2751d4fa73a004423a19c184017eab1d1c8`, built from the checked-in Dockerfile and dependency lockfile.

## Live Flue development workflow

The platform repaired an HTTP retry-delay helper in a real disposable GitHub repository. A committed baseline and its 15 tests were pushed before coding. Baseline checks failed both in a local verification container and on GitHub. Only `retry.py` could change.

The task required capped exponential backoff, integer and HTTP-date `Retry-After` handling, input validation, and safe arithmetic for large attempts. Five independent acceptance tests were frozen outside the worker snapshot before coding. They included 200 seeded cases, large attempts, timezone offsets, whitespace, and zero caps.

Flue coding and review agents used the configured model gateway. The durable coordinator sealed each candidate, ran the unchanged repository tests in a fresh container, and dispatched a separate review. Reviewer findings were independently reproduced before recording them as confirmed defects.

| Run | Observed result |
|---|---|
| First attempt, 18-request budget | Review found two real arithmetic defects across successive candidates. The last repair reached the request cap; the failed attempt and its receipts were preserved. |
| Follow-up, normal 30-request budget | Started from the same baseline with the two confirmed review findings included in the task. Review found a long numeric-header parsing defect, which triggered one automatic repair. |
| Follow-up completion | `ready_local` in **263.963 seconds**, **17 model requests**, **one repair**, and a clear final review. |
| Final independent verification | **15 frozen repository tests**, **five frozen independent tests**, and **three regressions added after review** all passed. |
| Completed resume | Repeating the identical workflow command returned the same result, events, and request count without new model work. |
| GitHub publication | The exact sealed patch was committed and pushed as a draft PR. Both branch and PR checks passed. Every remote tracked file and mode matched the candidate. |

The draft remains unmerged, with the failing baseline preserved. Publication was performed by the trusted operator through GitHub CLI after the platform returned its candidate. The live result therefore covers agent orchestration, verification, repair, receipt reuse, and publication of the exact output through that operator path.

| Evidence | Value |
|---|---|
| Baseline commit | `5c14dc2142ce6b3bb2bb71a6417a6bc535d432dc` |
| PR head commit | `43d0964aaf9918eb7fb8655d02c0abe52a295829` |
| Candidate SHA-256 | `0b0a0d70a0c0b3cc75026ef12cae24e1d678c3339a826a97f58586a27442df79` |
| Patch SHA-256 | `fa8391b2313d533732e0f91776b2f23d6ec1fc3133c1e7f0918db18076a7047c` |
| Frozen acceptance SHA-256 | `daba27069fe65d42bd15d723536b62bd61721d32c05db30dee4eecb4cf626ee4` |
| Changed paths | `retry.py` |
| Reported cost | Unavailable; retained as null |

Raw receipts, run journals, acceptance suites, reproduced findings, and remote verification records remain in private application state. Historical exercises are retained separately from this Flue implementation's evidence.

## Evaluation protocol

Compare a strong single coding agent, independent review with bounded repair, and selective read-only specialists under equal task and budget conditions. Freeze tasks, model configuration, images, recipe, evaluator revision, repetitions, and decision thresholds before a comparative run.

Preserve first attempts and repairs separately. Count infrastructure failures in attempted-work totals, retain paired task identities, and report missing usage explicitly. Keep hidden suites and acceptance policy outside worker access. Comparative claims require independent evaluation results; the exercise above records the observed behavior of one development task.
