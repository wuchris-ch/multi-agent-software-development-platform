# Verification record

Observed September 11, 2026 on macOS with Python 3.12.11, Node 22.22.3, Docker Engine 20.10.16, and Codex CLI 0.153.4. These results describe the tested executions and versions.

## Automated checks

The final full local suite passed **76 tests in 78.02 seconds**, using the coding image built from the checked-in Dockerfile: `sha256:a435bf7b4b17dcf41637c32c4ef07bc82d44b7da455f74e4a34ad8cc88a4093f`. Ruff checks and formatting passed. The wheel built and an isolated installation outside the checkout completed its Docker fixture as `ready_local`, including packaged migration and runner resources.

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv build
```

| Area | Observed assertion |
|---|---|
| SQLite | Atomic rollback, idempotent admission, payload conflict, stale version/epoch/lease rejection |
| Coordinator recovery | Twenty crash runs across four boundaries, plus process death, duplicate launch, dual service, and cancellation |
| Docker | Actual filesystem/environment canaries, blocked network, non-root execution, symlink rejection, resource/output deadlines, descendant termination |
| Snapshots | Clean-source enforcement, scope enforcement, additions/deletions, content and artifact validation |
| Flue | Blocked exit-zero response, digest/hunk/schema/severity checks, bounded subprocess I/O |
| Codex broker | Actual CLI against fake Responses streams, tool execution in the sandbox, host credential canary exclusion, quota termination, receipt reuse |
| Candidate workflow | Fresh verification, review binding, bounded repairs, stale evidence, interrupted intake after a completed coding receipt |
| Integration contracts | Candidate/decision substitution, expired authorization, paginated fake-remote reconciliation, lost-response ambiguity |

The automated suite makes no paid model calls and writes no GitHub changes. Docker integration checks need a reachable daemon and the prepared coding image. `scripts/smoke_installed.py` checks a wheel installed in a fresh environment outside the checkout. The checked-in CI workflow runs these steps with pinned action revisions; its GitHub-hosted run has not been exercised because the repository has not been pushed.

## Real repository exercise

Source: `pr-review-agent-flue` at `024f477e613de41a23a4b6a8986c735f703bbdae`. Work happened in a content-only disposable workspace. The original checkout and running watcher were preserved.

The candidate binds diff fetching and review publication to captured base/head revisions. It includes the reviewed commit in the publication payload, checks the current revisions before reporting success, validates the published review's commit, and marks stale outcomes explicitly. Completion markers include base and head identity.

Six new race tests use a local fake GitHub server. All six fail against the baseline with only exports added to expose the tested entry points. On the candidate, all **74 tests pass**, together with TypeScript type checking and compilation. A fresh one-shot Flue review of the exact diff returned no findings in 27.872 seconds. A second verification through the candidate workbench passed, and inspection returned `ready_local`.

| Artifact | SHA-256 |
|---|---|
| Candidate manifest | `bcfd2e18d55828bb066b4dc583dca3f4e63ba74005a967cf4b87f8c9fc5f88f4` |
| Binary patch | `fe590d6762e4ec5f89e78f2ab6c6dc737d4c0762567a6b0b87cf6608a69bf102` |
| Verification image | `cbbec424e7cba35a1a9ff397fc084cb350866275b30976a4e61774aea32aee68` |

The exercise establishes behavior against those fake-server interleavings. It did not publish a review or patch to live GitHub. The patch was prepared during implementation and imported through the workbench; it is not presented as a Codex-generated benchmark result. Artifacts and detailed logs remain in private application state.

## Live coding exercise

A brokered Codex worker fixed a calculator subtraction bug with only `calculator.py` writable. The original test file remained fixed. Five model requests returned HTTP 200, the CLI completed, and a separate credential-free verification container passed the addition test with positive and mixed-sign inputs.

The observed elapsed time was **29.282 seconds**, including independent verification. CLI-reported usage was 28,224 input tokens and 626 output tokens, with cached/reasoning fields preserved in the private receipt. Cost was not reported and remains null. The worker used a Keychain-backed host profile and an external-network-disabled container. Its image was `sha256:9a72f4e1ed563ee949496b0b345c8c47559beef87130c93ab3ae9a49789f1d18`.

This proves the tested gateway/CLI path can produce a candidate. The separate fake-model isolation tests verify the credential, filesystem, network, and quota boundaries without exposing a real credential.

## Evaluation protocol

The independent evaluator will compare a strong single coding worker, independent review with bounded repair, and selective read-only specialists under equal task and budget conditions. Freeze tasks, model configuration, images, recipe, evaluator revision, repetitions, and practical decision thresholds before a run.

Preserve first attempts and repairs separately. Count infrastructure failures in overall attempted-work totals, retain paired task identities, and report missing usage explicitly. Hidden suites and acceptance policy remain outside worker access. Published comparative claims require actual independent results; the exercises above are engineering evidence for their specific workflows.
