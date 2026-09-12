# Implementation guide

`workflow run` orchestrates a repository task through Flue coding, fresh checks, independent Flue review, and bounded repairs. `candidate` commands expose the same primitives for individual stages. State stays outside the source checkout.

## Agent setup

Build the locked agent package in its execution image:

```sh
uv sync --frozen
docker build -t swe-platform-flue:local -f containers/flue.Dockerfile .
export SWE_PLATFORM_AGENT_IMAGE="$(docker image inspect swe-platform-flue:local --format '{{.Id}}')"
```

The image pins Node and installs the committed npm lockfile, including Flue 2.0.3. Execution requires an immutable image digest, selected by `SWE_PLATFORM_AGENT_IMAGE` or `--image`. Record the resulting digest when comparing runs. No external coding CLI or separate review repository is required.

## Model gateway

Keep gateway profiles outside source checkouts. Use a gateway that supports the Chat Completions API and the selected model's tool calling. A profile names the endpoint, model, and an explicit credential source:

```json
{
  "schema_version": "gateway-profile/v1",
  "base_url": "https://gateway.example.invalid/v1",
  "model": "configured-model-id",
  "api_key_env": "MODEL_GATEWAY_API_KEY"
}
```

Supply the named environment variable through your normal secrets configuration. On macOS, a profile can instead use `"keychain_service": "configured-service"`; select exactly one credential source. Existing Keychain profiles remain supported. Credentials are resolved by the trusted host transport, not by agents or repository code.

Use `--review-profile review-gateway.json` to select a different model for review. Without it, both roles use the supplied gateway profile. The agent definitions and workflow policy do not depend on a particular model vendor.

## Verification recipe

Prepare dependencies in a trusted image before execution. A Python recipe is:

```json
{
  "schema_version": "verification-recipe/v1",
  "image": "python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea",
  "argv": ["python", "-m", "unittest", "discover", "-v"],
  "timeout": 60
}
```

Recipes are frozen when the job is admitted. Each candidate is tested in a fresh environment. Exclude the test files from allowed coding paths when they must remain fixed. Hidden acceptance checks stay outside the workflow and its agent snapshots.

## Workflow commands

```sh
uv run swe-platform workflow run /path/to/repo recipe.json gateway.json \
  --key fix-pagination --allow src/pagination.py \
  --task 'Fix the reported pagination failure and run the public checks' \
  --timeout 600 --max-requests 30 --max-repairs 2
uv run swe-platform workflow inspect fix-pagination
uv run swe-platform workflow cancel fix-pagination
```

Repeat the identical run command to resume. A reused key with different task, scope, image, gateway identity, or budget is rejected. The initial source snapshot remains the baseline even if the original checkout later advances.

Coding stages reserve up to 12 requests, review stages up to two, within the shared job cap. Each request has an output-token limit of 4,096. Unused reservations are released after a saved result; unresolved requests remain reserved. Cost is null when the gateway does not report it. The workflow keeps one absolute deadline across stages and restarts.

A repair receives the previous candidate and the public-check output or blocking review, with bounded feedback. It does not receive hidden tests. After the configured repair count, a still-blocked task becomes `needs_attention`.

## Individual stages

```sh
uv run swe-platform candidate import /path/to/repo change.patch recipe.json --allow src/example.py
uv run swe-platform candidate code /path/to/repo recipe.json gateway.json \
  --key example-fix --allow src/example.py --task 'Fix the reported failure'
uv run swe-platform candidate verify <digest>
uv run swe-platform candidate review <digest> gateway.json
uv run swe-platform candidate inspect <digest>
uv run swe-platform candidate repair <digest> replacement.patch
```

Sources must be clean. Snapshots accept regular committed files, with an 8 MiB input ceiling. Symlinks, submodules, Git LFS pointers, and tracked `.env` files are rejected. Candidate output is limited to 256 KiB across the allowed paths. Review receives a complete bounded diff; oversized input is rejected rather than silently truncated.

Workflow candidates live under that workflow's evidence namespace. Use `workflow inspect` to locate their patch and evidence; standalone candidate commands use the top-level candidate namespace.

## Recovery

| Situation | Behavior |
|---|---|
| Completed workflow or stage | The same key reuses saved results without another model call. |
| Coding result saved before intake interruption | Resume seals the saved output and continues verification. |
| Agent intent without a result | Reconcile or cancel before starting an intentional new attempt under a new key. |
| Verification interrupted | The labeled execution is adopted or collected; it is not replaced blindly. |
| Cancel while an agent or checks are running | Persist the request, stop the active container, prevent subsequent stages, and confirm termination. |
| Docker unavailable | Report unconfirmed termination and retain the stage intent. |

The fixture service still supports `serve`, `submit`, `status`, and `cancel` for deterministic supervisor and SQLite recovery tests. It is separate from the repository workflow command.

## Control panel and draft publication

Build the web assets with `npm --prefix web ci --ignore-scripts` and `npm --prefix web run build`, then run `uv run swe-platform ui`. The CLI prints separate operator and viewer URLs. The session token moves from the launch fragment into session storage; requests authenticate through a bearer header. The service listens on loopback. Use `--port` to select another port.

The Publication tab prepares an immutable destination and commit for review. An operator approves that plan to create a draft PR. The CLI exposes the same operations:

```sh
uv run swe-platform publication prepare <candidate> --workflow fix-pagination \
  --repository owner/repository --branch development/fix-pagination \
  --title 'Fix pagination' --body-file description.md
uv run swe-platform publication publish <plan-digest> --workflow fix-pagination
uv run swe-platform publication reconcile <plan-digest> --workflow fix-pagination
```

GitHub CLI must already be authenticated with repository write access. Preparation verifies repository and actor identities and the unchanged base. Plans expire after 24 hours for new writes; recovery reads remain possible after expiration. Reconciliation never creates another PR. If an uncertain dispatch has no matching remote result, retain the saved intent and investigate that operation before preparing another delivery.

`workflow export <key> evidence.json` creates a bounded portable bundle. `evidence verify evidence.json --expected <sha256>` checks its bytes and candidate bindings without a running service. Keep the expected digest from a trusted source when transporting the bundle.

## Development checks

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

CI builds the React assets before the wheel and installs that wheel into a clean environment outside the checkout. The installed smoke test runs a disposable Docker fixture and checks the packaged console over HTTP.
