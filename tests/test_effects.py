import pytest

from swe_platform.effects import reconcile
from swe_platform.models import State, Submission
from swe_platform.store import Store


class FakeRemote:
    def __init__(self):
        self.records = {}
        self.calls = 0

    def lookup(self, key):
        return self.records.get(key)

    def create(self, key, request):
        self.calls += 1
        self.records[key] = f"remote-{self.calls}"
        return self.records[key]


def test_lost_response_reconciles_without_second_effect(tmp_path):
    store = Store(tmp_path)
    job = store.submit(Submission(key="external"))
    store.transition(job["id"], 0, State.implementing, effect=("fake_publish", "target", {}))
    effect_id = store.inspect(job["id"])["effects"][0]["id"]
    remote = FakeRemote()

    def crash(point):
        if point == "after_remote":
            raise RuntimeError("lost response")

    with pytest.raises(RuntimeError):
        reconcile(store, effect_id, remote, crash)
    assert reconcile(store, effect_id, remote) == "remote-1"
    assert reconcile(store, effect_id, remote) == "remote-1"
    assert remote.calls == 1
    store.close()


def test_uncertain_absence_does_not_repeat(tmp_path):
    store = Store(tmp_path)
    job = store.submit(Submission(key="uncertain"))
    store.transition(job["id"], 0, State.implementing, effect=("fake_publish", "target", {}))
    effect_id = store.inspect(job["id"])["effects"][0]["id"]
    store.db.execute("UPDATE effects SET state='in_flight'")
    remote = FakeRemote()
    assert reconcile(store, effect_id, remote) is None
    assert remote.calls == 0
    store.close()
