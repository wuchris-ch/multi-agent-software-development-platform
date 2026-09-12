# Implementation guide

The CLI supports restartable fixture jobs through `serve`, and repository candidates through `candidate`. Both keep state outside source checkouts. Run `uv run swe-platform --help` and `uv run swe-platform candidate --help` for the installed command surface.

## Coding setup

Build the trusted worker image from the pinned Node base and Codex CLI 0.153.4:

```sh
docker build -t swe-platform-codex:local -f containers/codex.Dockerfile containers
export SWE_PLATFORM_CODING_IMAGE="$(docker image inspect swe-platform-codex:local --format '{{.Id}}')"
```

Execution requires an immutable image digest. The environment variable selects the locally built image; `candidate code --image sha256:...` selects it per invocation. Node and Codex are pinned in the Dockerfile. OS packages resolve during the build, so record the resulting image ID when comparing runs. Image preparation uses registry access; execution containers use `--network none`.

Create a private gateway profile outside source checkouts, with mode `600`. The credential belongs in macOS Keychain under the profile's service name:

```json
{
  "schema_version": "gateway-profile/v1",
  "base_url": "https://gateway.example.invalid/v1",
  "model": "configured-model-id",
  "keychain_service": "configured-keychain-service"
}
```

The host resolves the credential and passes it to a trusted HTTP transport through an anonymous pipe. The worker sees a loopback endpoint and the model alias `worker`. Coding needs a compatible Responses endpoint; Flue uses its existing supported transport. Repository files do not choose privileged endpoints or recipes.

## Verification recipes

Prepare dependencies in a trusted image before execution. A Python example is:

```json
{
  "schema_version": "verification-recipe/v1",
  "image": "python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea",
  "argv": ["python", "-m", "unittest", "discover", "-v"],
  "timeout": 60
}
```

Recipes are fixed at intake and stored separately from worker files. Tests run in a fresh container from the sealed candidate. Repository tests are public checks; exclude them from the writable scope when they must remain unchanged. Independent acceptance belongs to the evaluator.

## Candidate commands

```sh
uv run swe-platform candidate import /path/to/repo change.patch recipe.json --allow src/example.py
uv run swe-platform candidate code /path/to/repo recipe.json gateway.json \
  --key example-fix --allow src/example.py --task 'Fix the reported failure' \
  --timeout 180 --max-requests 12
uv run swe-platform candidate verify <digest>
uv run swe-platform candidate review <digest> /path/to/flue/dist/cli.js gateway.json
uv run swe-platform candidate inspect <digest>
uv run swe-platform candidate repair <digest> replacement.patch
```

Sources must be clean. Snapshots include committed regular files, with an 8 MiB input ceiling. Symlinks, submodules, Git LFS pointers, and tracked `.env` files are rejected. The original checkout is never the worker's workspace. Model output is limited to 256 KiB across allowed paths; other generated files are discarded. Changes to existing files outside the scope are rejected.

Coding defaults to 180 seconds, 12 requests, and at most 4,096 output tokens per request. Requests rejected by the gateway consume the request allowance. Usage is recorded when the CLI supplies it; `cost_usd: null` means unreported cost. Repairs are replacement patches against the original base. A lineage allows two repairs, rejects repeated candidates, and marks earlier candidates stale.

## Recovery

| Situation | Action and behavior |
|---|---|
| Coordinator stopped during a fixture job | Restart `serve` with the same state. The supervisor reconciles its saved attempt. |
| Coding has a durable receipt | Repeat the same command and key. Saved output is reused, including after interruption during candidate intake. |
| Coding dispatch has no receipt | Run `candidate stop-coding <key>`. Inspect retained private state; use a new key for an intentional new attempt. |
| Review intent has no verdict | Reconcile the invocation before another billable call. The command will not silently repeat it. |
| Verification interrupted | Repeat `candidate verify`. The labeled container is collected or adopted; saved receipts are reused. |
| Docker unavailable during cancellation | Restore daemon access and repeat cancellation. A stopped attach process alone is not confirmation. |

Run `uv run pytest -q tests/test_recovery.py tests/test_broker.py tests/test_workbench.py` for recovery exercises. Fixture fault points cover intent persistence, worker launch, artifact persistence, and final completion. Coding recovery uses durable stage receipts rather than replaying a model conversation.

## Development checkpoints

Implemented foundations include durable fixture execution, constrained Docker workers, content snapshots, brokered coding, independent review, bounded repair, and local delivery. Evaluator and publication contracts have offline substitution and reconciliation tests.

The next sequence is a unified job view for candidate stages, explicit review reconciliation tooling, an authenticated evaluator exchange, and publication under exact-candidate authorization. Read-only specialists and routing experiments follow a frozen evaluator comparison. Revisit this sequence when actual usage reveals a more valuable dependency.

Keep commits focused on working behavior and the tests that establish it. Preserve actual versions, meaningful failures, migration decisions, and accurate commit dates.
