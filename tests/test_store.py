import sqlite3

import pytest

from swe_platform.io import Artifacts
from swe_platform.models import Completion, Conflict, Fenced, State, Submission
from swe_platform.store import Store


def test_submission_identity_and_atomic_rollback(tmp_path):
    store = Store(tmp_path)
    spec = Submission(key="same")
    job = store.submit(spec)
    assert store.submit(spec)["id"] == job["id"]
    with pytest.raises(Conflict):
        store.submit(spec.model_copy(update={"task": "different"}))
    store.db.execute(
        "CREATE TRIGGER break_effect BEFORE INSERT ON effects BEGIN SELECT RAISE(ABORT, 'fault'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.launch_intent(job["id"], "owner")
    assert store.get(job["id"])["state"] == "queued"
    assert store.attempt(job["id"]) is None
    assert len(store.inspect(job["id"])["events"]) == 1
    store.close()


def test_transitions_and_fencing(tmp_path):
    clock = [100]
    store = Store(tmp_path, clock=lambda: clock[0])
    job = store.submit(Submission(key="fence"))
    with pytest.raises(Conflict):
        store.transition(job["id"], 0, State.ready_local)
    attempt = store.launch_intent(job["id"], "one")
    completion = Completion(attempt_id=attempt["id"], epoch=1, outcome="completed")
    with pytest.raises(Fenced):
        store.complete(job["id"], "two", completion)
    with pytest.raises(Fenced):
        store.complete(job["id"], "one", completion.model_copy(update={"epoch": 0}))
    clock[0] = 131
    with pytest.raises(Fenced):
        store.complete(job["id"], "one", completion)
    store.adopt(job["id"], "new")
    with pytest.raises(Fenced):
        store.complete(job["id"], "one", completion)
    store.complete(job["id"], "new", completion)
    with pytest.raises(Conflict):
        store.complete(job["id"], "new", completion)
    store.close()


def test_artifact_tampering(tmp_path):
    artifacts = Artifacts(tmp_path)
    sha = artifacts.put(b"candidate")
    (tmp_path / sha).chmod(0o600)
    (tmp_path / sha).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        artifacts.get(sha)
    with pytest.raises(ValueError):
        artifacts.get("../secret")
