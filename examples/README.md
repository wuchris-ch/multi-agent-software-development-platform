# Recoverable workflow replay

This exercise runs the production Flue runtime, broker, workflow coordinator, verifier and evidence exporter. A fixed transcript supplies model responses. It requires Docker and the built agent image, with no model credentials or remote repository writes.

From the project checkout:

```sh
uv sync --frozen
docker build -t swe-platform-flue:local -f containers/flue.Dockerfile .
export SWE_PLATFORM_AGENT_IMAGE="$(docker image inspect swe-platform-flue:local --format '{{.Id}}')"
uv run python examples/replay.py /tmp/development-replay
```

Choose a new directory outside the checkout. The replay creates its own source repository and state there. It only changes the private worker's allowed `value.py` file; the source and its existing test remain unchanged.

The expected sequence is:

1. A Flue coding agent writes the first candidate and saves its receipt.
2. The process exits with code 86 before candidate intake.
3. A fresh process resumes from the receipt without repeating coding.
4. The public test rejects the value of two. One repair changes it to three.
5. Fresh verification and a separate review complete the current candidate.
6. A completed resume returns the same result, with five fixture requests total.

`result.json` records the candidate and recovery checks. `evidence.json` contains the exact patch, public verification, review and linked trace. Token usage stays unknown because this transport does not supply real model usage.

```sh
uv run swe-platform evidence verify /tmp/development-replay/evidence.json
uv run python examples/replay.py /tmp/development-replay
```

To inspect the saved run in the control panel, build the web assets and open the URL printed by the console command:

```sh
npm --prefix web ci --ignore-scripts
npm --prefix web run build
uv run swe-platform --state /tmp/development-replay/state ui
```
