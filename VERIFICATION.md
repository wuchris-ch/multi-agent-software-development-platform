# Verification record

Observed September 11, 2026 with Flue runtime 2.0.3, Node 22.22.3, Python 3.12.11, and Docker Engine 20.10.16 on macOS. GitHub CI independently exercised the platform on Linux.

## Automated checks

[CI at `73777f8`](https://github.com/wuchris-ch/multi-agent-software-development-platform/actions/runs/34675704324) passed **153 Python tests in 92.76 seconds**, **eight React tests**, the Node provider test, TypeScript checking, lint, formatting and package build. The installed wheel completed its Docker fixture outside the checkout, served the packaged console over authenticated HTTP and verified all seven pinned evaluator schemas.

```sh
npm ci --ignore-scripts
npm test
npm --prefix web ci --ignore-scripts
npm --prefix web test
npm --prefix web run build
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv build
```

The Docker integration tests run the actual Flue runtime against controlled model responses. They exercise tool execution, review, repair, cancellation, and recovery without paid model requests or GitHub writes.

CI also runs the [process-recovery replay](examples/README.md) twice. A real process exits after its coding receipt; a fresh process reuses that receipt, repairs a failing public check and completes review. The completed replay stays at five fixture requests and exports the same verified evidence bundle on repetition. Selective-mode integration checks exercise two concurrent read-only specialists and one coding agent. Policy tests enforce frozen initial pairs, reserved held-out families and generation-fenced rollback.

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

## Publication recovery and packaged console, September 11, 2026

The platform's live publisher prepared a new draft from the sealed candidate above, then the process exited immediately after GitHub created the PR and before its receipt was saved. A fresh process performed **16 GET requests and no writes**, adopted exactly one matching draft, verified all remote content and ancestry, and saved the recovered receipt. Both branch and PR checks passed for head `9ffe745d782c4b4668e0d7965c9ba0a0a0f063d0`.

The Python suite passed **124 tests**, including publication ambiguity, pagination, actor/branch/content substitution, cancellation races, evidence export and console permissions. The React suite passed **six tests**, followed by TypeScript checking and a production build. Browser inspection used real saved workflow data to verify the candidate diff, public checks, independent review and recovered PR status. The installed wheel smoke runs outside the checkout and validates its Docker fixture, packaged React assets and authenticated console API.

## Comparison protocol

Compare a strong single coding agent, independent review with bounded repair, and selective read-only specialists under equal task and budget conditions. Freeze tasks, model configuration, images, recipe, evaluator revision, repetitions, and decision thresholds before a comparative run.

Preserve first attempts and repairs separately. Count infrastructure failures in attempted-work totals, retain paired task identities, and report missing usage explicitly. Keep hidden suites and acceptance policy outside worker access. Comparative claims require independent evaluation results; the repository exercises here record individual development tasks.

## Existing-repository parser repair, September 12, 2026

An isolated copy of [the Flue review agent](https://github.com/wuchris-ch/pr-review-agent-flue) at `fcb314d2568dc7ea4a5e3c3d175704cc03fca77d` supplied a real parser defect. Git-quoted filenames were omitted from the file set used to attach review findings. Only `src/diff.ts` could change; tests and dependencies remained fixed.

The review-mode workflow completed in **122.094 seconds**, using **11 model requests**, **65,358 reported tokens** and **zero repairs**. A separate Flue reviewer returned a clear review. All three existing public diff checks passed, and every model request had resolved usage. The invocation retained its 20-request, 200,000-token and 600-second bounds.

Seven actual Git-generated patches covered plain filenames, spaces, tabs, quotes, backslashes and two Unicode filenames. The independent suite checked both header forms, CRLF input, deleted files, malformed quoting and exact whole-file partition text. The original implementation passed **8 of 24 checks**; the sealed candidate passed **24 of 24**.

The first assessment reported 23 passes because Node's strict assertion compared arrays from different VM contexts. A minimal harness correction normalized the returned array before comparison; expected filenames, patch fixtures and text assertions stayed unchanged. Both assessment versions were retained. The corrected harness still rejected all 16 baseline failures, and the candidate received no edits or additional model calls.

Repeating the completed workflow preserved the job, model ledger and exported evidence bundle byte for byte. Offline bundle verification passed.

The platform publisher created a draft PR against the unchanged baseline in a separate private exercise repository. GitHub CI at head `1b3fa18759c691a761caa7815d2dba0071106326` passed **97 repository tests**, TypeScript checking, regression fixtures, dependency audit and build. A fresh reconciliation made **15 GET requests and no writes**, verified the exact remote files, modes and ancestry, and confirmed passing checks. The draft remains unmerged; the original source repository and its running reviewer were untouched.

| Evidence | SHA-256 |
|---|---|
| Candidate | `0366b058d33e61e2bc72a71fb69f7f8e447eeb64d72f4e55ba4763e814c495fd` |
| Frozen patch cases | `34bb8b6270665f4bd2a67f1b77c8df4d8dfebde415843682a347b8121275cb2e` |
| Original acceptance harness | `7aa2b6026cee8d33506d55db6a86d5584e70a2a79b3c6216ed67c0c16dd8cbdf` |
| Corrected acceptance harness | `e53134ef0c6dd2001f2cc0506baba5b804110144ee626519c668816f0682fdfb` |
| Portable evidence bundle | `21a0cbffd5b86bca691c68c4535e202e0776a8898156de592915cd461ad2f55e` |
