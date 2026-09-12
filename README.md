# Multi-agent software development platform

[![Verification](https://github.com/wuchris-ch/multi-agent-software-development-platform/actions/workflows/verify.yml/badge.svg)](https://github.com/wuchris-ch/multi-agent-software-development-platform/actions/workflows/verify.yml)

Flue agents that turn repository tasks into reviewed, verified changes.

A coding agent implements the change. An independent review agent checks the patch. A resumable workflow coordinates their work, runs the repository's tests, and feeds failures back into bounded repair attempts. Each result stays tied to the exact candidate that produced it.

## Run a development task

Use Python 3.12+, Node 22.19+, uv, Git, and Docker. Prepare a [model gateway profile and verification recipe](IMPLEMENTATION.md), then build the agent image:

```sh
uv sync --frozen
docker build -t swe-platform-flue:local -f containers/flue.Dockerfile .
export SWE_PLATFORM_AGENT_IMAGE="$(docker image inspect swe-platform-flue:local --format '{{.Id}}')"

uv run swe-platform workflow run /path/to/repo recipe.json gateway.json \
  --key fix-pagination --allow src/pagination.py \
  --task 'Fix pagination and run the existing tests'
```

The workflow snapshots the repository, runs the Flue coding agent, verifies the candidate in a fresh environment, and requests an independent Flue review. Failed checks or blocking findings can trigger up to two repair attempts within the same request budget and deadline.

```sh
uv run swe-platform workflow inspect fix-pagination
uv run swe-platform workflow cancel fix-pagination
```

Repeat the original command to resume. Completed stages reuse their saved results. Inspection shows the current patch, checks, review, repair history, and model-request budget. A passing current candidate is `ready_local`.

## Work one stage at a time

The candidate commands support existing patches and custom workflows:

```sh
uv run swe-platform candidate code /path/to/repo recipe.json gateway.json \
  --key example-fix --allow src/example.py --task 'Fix the reported failure'
uv run swe-platform candidate verify <candidate-digest>
uv run swe-platform candidate review <candidate-digest> gateway.json
uv run swe-platform candidate inspect <candidate-digest>
```

Use `candidate import` for an existing patch and `candidate repair` for a replacement against the original base. Coding and review use the same Flue runtime, with separate roles and capabilities. Model selection is configured independently for each role.

## Project guide

| Document | Contents |
|---|---|
| [Architecture](ARCHITECTURE.md) | Agent roles, orchestration, state, and execution boundaries |
| [Implementation](IMPLEMENTATION.md) | Setup, configuration, commands, and recovery |
| [Contracts](CONTRACTS.md) | Durable records and adapter interfaces |
| [Evaluator integration](INTEGRATION.md) | Independent acceptance and evaluation |
| [Verification](VERIFICATION.md) | Reproducible checks and observed repository exercises |

## Development

```sh
npm ci --ignore-scripts
npm test
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv build
```

Build the Flue image before running the integration suite. Automated tests use fixture model responses and disposable containers; live model exercises are recorded separately.
