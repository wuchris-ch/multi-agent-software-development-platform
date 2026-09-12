import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_console import console as console
from test_github_publication import publication as publication
from test_snapshot import repository as repository

from swe_platform.io import atomic_write, canonical, digest
from swe_platform.producer import (
    BINDINGS,
    IDENTITY,
    EvaluatorClient,
    EvaluatorProfile,
    Producer,
    validate_wire,
)

FIXTURES = Path(__file__).parent / "fixtures/evaluator-v2"


def fixture(name):
    return json.loads((FIXTURES / (name + ".json")).read_bytes())


def test_pinned_evaluator_fixtures_validate_and_reject_contract_drift():
    for name, model in (
        ("ticket", "TrialTicket"),
        ("execution", "ExecutionContract"),
        ("submission", "Submission"),
        ("assessment", "Assessment"),
    ):
        value = fixture(name)
        validate_wire(model, value)
        with pytest.raises(ValueError, match="contract"):
            validate_wire(model, {**value, "unexpected": True})
    root = Path(__file__).parents[1] / "src/swe_platform/contracts/v2"
    pin = json.loads((root / "pin.json").read_bytes())
    assert pin["source_revision"] == "e028f37195cbd8519b80d7b2e20af443ec57502f"
    for name, sha in pin["files"].items():
        assert digest((root / name).read_bytes()) == sha


class FixtureEvaluator:
    def __init__(self):
        self.profile = SimpleNamespace(evaluator_sha256=fixture("ticket")["evaluator_sha256"])
        self.contract = self.submitted = None
        self.artifacts = {}
        self.assessment_update = {}
        self.lose_receipt = False

    def verify_authority(self):
        pass

    def call(self, path, body=None):
        if path.startswith("/v1/execution-contracts/"):
            return self.contract
        if path == "/v1/producer-artifacts":
            stored = digest(canonical(body))
            self.artifacts[stored] = body
            return {"sha256": body["content_sha256"], "storage_key": stored}
        if path == "/v1/submissions":
            if self.submitted is not None:
                assert canonical(self.submitted) == canonical(body)
            self.submitted = body
            if self.lose_receipt:
                self.lose_receipt = False
                raise TimeoutError("lost response after durable intake")
            return {
                "execution_id": body["execution_id"],
                "submission_sha256": digest(canonical(body)),
                "status": "awaiting_independent_evaluation",
            }
        if path.startswith("/v1/assessments/"):
            template = fixture("assessment")
            return {
                **template,
                **{
                    field: self.submitted[field]
                    for field in (*IDENTITY, *BINDINGS, "execution_contract_sha256")
                },
                "submission_sha256": digest(canonical(self.submitted)),
                **self.assessment_update,
            }
        raise AssertionError(path)


def prepared(console):
    _, _, _, _, directory = console
    client = FixtureEvaluator()
    producer = Producer(directory.parents[1], client)
    ticket = fixture("ticket")
    record = json.loads((directory / "job.json").read_bytes())
    key = record["key"]
    ticket["base_revision"] = record["base"]["base_revision"]
    atomic_write(
        directory / "producer/admission.json",
        canonical({"ticket": ticket, "recipe": {}, "request_sha256": "a" * 64}),
    )
    client.contract = producer.binding(key)
    return producer, client, key, directory


def test_submission_is_not_acceptance_and_lost_intake_receipt_reuses_exact_outbox(console):
    producer, client, key, directory = prepared(console)
    client.lose_receipt = True
    with pytest.raises(TimeoutError):
        producer.submit(key)
    first = (directory / "producer/submission.json").read_bytes()
    result = producer.submit(key)
    assert result["state"] == "awaiting_independent_evaluation"
    assert not (directory / "producer/assessment.json").exists()
    assert (directory / "producer/submission.json").read_bytes() == first
    assert len(client.artifacts) == 5
    accepted = producer.assessment(key)
    assert accepted["state"] == "accepted"
    assert producer.assessment(key) == accepted


def test_artifacts_upload_before_operator_can_issue_a_contract(console):
    producer, client, key, directory = prepared(console)
    contract = client.contract
    client.contract = None
    uploaded = producer.upload(key)
    assert uploaded["state"] == "artifacts_registered"
    assert uploaded["candidate_binding"] == contract
    assert len(client.artifacts) == 5 and client.submitted is None
    assert not (directory / "producer/submission.json").exists()
    client.contract = contract
    assert producer.submit(key)["state"] == "awaiting_independent_evaluation"


@pytest.mark.parametrize(
    "field",
    [
        "candidate_tree_sha256",
        "policy_sha256",
        "evaluator_sha256",
        "trial_ticket_sha256",
        "submission_sha256",
    ],
)
def test_assessment_substitution_is_rejected(console, field):
    producer, client, key, directory = prepared(console)
    producer.submit(key)
    client.assessment_update = {field: "f" * 64}
    with pytest.raises(ValueError, match="does not match"):
        producer.assessment(key)
    assert not (directory / "producer/assessment.json").exists()


def test_authenticated_transport_pins_authority_and_cannot_issue_contracts(monkeypatch):
    observed = []
    sha = "a" * 64

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append(
                (self.path, self.headers.get("Authorization"), self.headers.get("X-Project"))
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(canonical({"contract_version": 2, "evaluator_sha256": sha}))

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    monkeypatch.setenv("TEST_EVALUATOR_TOKEN", "private-token-canary")
    client = EvaluatorClient(
        EvaluatorProfile(
            base_url=f"http://127.0.0.1:{server.server_port}",
            project="fixture",
            evaluator_sha256=sha,
            token_env="TEST_EVALUATOR_TOKEN",
        )
    )
    try:
        client.verify_authority()
        assert observed == [("/v1/authority", "Bearer private-token-canary", "fixture")]
        for path in (
            "/v1/trial-tickets",
            "/v1/execution-contracts",
            "/v1/submissions/test/evaluate",
        ):
            with pytest.raises(ValueError, match="outside producer authority"):
                client.call(path, {})
        sha = "b" * 64
        with pytest.raises(ValueError, match="identity changed"):
            client.verify_authority()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
