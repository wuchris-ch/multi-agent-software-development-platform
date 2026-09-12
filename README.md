# Multi-agent software engineering platform

[![Verification](https://github.com/wuchris-ch/multi-agent-software-engineering-platform/actions/workflows/verify.yml/badge.svg)](https://github.com/wuchris-ch/multi-agent-software-engineering-platform/actions/workflows/verify.yml)

Turn a repository task into an inspectable patch, with isolated coding, independent review, and evidence tied to the exact candidate.

The platform coordinates a coding worker and a fresh Flue reviewer. Python, SQLite, and Docker handle execution, recovery, and verification. Candidate workspaces contain repository content; model credentials stay on the host in macOS Keychain.

## What it does

- Runs Codex CLI in a container with external networking disabled. A host broker permits bounded model requests through attached process pipes.
- Imports or generates patches from clean repository snapshots and enforces an explicit output scope.
- Runs a pinned verification recipe on a fresh candidate snapshot and validates Flue's verdict against the exact diff.
- Preserves attempts and receipts, rejects conflicting submission keys, and limits each lineage to two repairs.
- Exercises coordinator crashes, stale ownership, cancellation, output limits, and uncertain external effects through deterministic fixtures.

## Get started

Use Python 3.12+, uv, Git, and Docker. Coding and Flue review also use Node 22+. The default state directory is `~/Library/Application Support/SWEPlatform`.

```sh
uv sync --frozen
uv run swe-platform doctor
uv run swe-platform init
uv run swe-platform serve
```

In another terminal, run a fixture job:

```sh
uv run swe-platform submit --key first-fixture --adapter docker-scripted
uv run swe-platform status
uv run swe-platform inspect <job-id>
```

Restarting `serve` reconciles saved fixture executions. Use `cancel <job-id>` to request termination and inspect its confirmed state.

## Work on a repository

Prepare the [coding image and private gateway profile](IMPLEMENTATION.md#coding-setup), then select a clean source repository, allowed paths, and trusted verification recipe:

```sh
uv run swe-platform candidate code /path/to/repo recipe.json gateway.json \
  --key fix-addition --allow src/calculator.py \
  --task 'Correct addition and run the existing tests'

uv run swe-platform candidate verify <candidate-digest>
uv run swe-platform candidate review <candidate-digest> /path/to/flue/dist/cli.js gateway.json
uv run swe-platform candidate inspect <candidate-digest>
```

`inspect` returns the local patch path, checks, review, and repair count. A current candidate with passing checks and a clear review is `ready_local`. Repeating a completed coding key reuses its saved result. `candidate stop-coding <key>` stops an interrupted coding execution.

To start with an existing patch, use `candidate import /path/to/repo change.patch recipe.json --allow src/calculator.py`. Use `candidate repair <digest> replacement.patch` for a revised patch against the original base. Each revision gets fresh evidence.

## Verified examples

A disposable GitHub exercise took a batching bug from a failing baseline to a reviewed draft PR. The unchanged candidate passed 14 frozen repository tests, five independent checks, and GitHub CI. A repeated coding command reused the saved result.

A local Flue watcher candidate reproduced six stale-review races against a fake GitHub server. All six failed against the original code; the candidate passed 74 tests, TypeScript checks, and a fresh Flue review. A live brokered Codex run also fixed a calculator fixture, which passed verification in a separate container. See the [verification record](VERIFICATION.md) for versions, scope, and reproducible checks.

## Project guide

| Document | Contents |
|---|---|
| [Implementation guide](IMPLEMENTATION.md) | Images, recipes, commands, recovery, and development |
| [Architecture](ARCHITECTURE.md) | Execution, credentials, evidence, and ownership |
| [Contracts](CONTRACTS.md) | Durable records and adapter interfaces |
| [Evaluator integration](INTEGRATION.md) | Independent evaluation boundary and proposed exchange format |
| [Verification](VERIFICATION.md) | Automated checks and observed exercises |
| [Research](RESEARCH.md) | Source-backed design decisions |

## Development

Build the coding image described in the implementation guide before running the integration tests.

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv build
```

The test suite uses local fixtures, fake model responses, and disposable containers. Live model exercises are separate from the automated suite.
