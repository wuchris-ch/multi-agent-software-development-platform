"""Reconciliation algorithm for fake/local effects. No live publisher exists."""

import json


def reconcile(store, effect_id, remote, fault=lambda _: None):
    effect = dict(store.db.execute("SELECT * FROM effects WHERE id=?", (effect_id,)).fetchone())
    if effect["kind"] == "execute":
        raise ValueError("Execution effects belong to the supervisor")
    if effect["state"] == "confirmed":
        return effect["remote_id"]
    found = remote.lookup(effect["target"])
    if found is None:
        if effect["state"] != "pending":
            # Absence after uncertain dispatch is not proof the remote request failed.
            return None
        with store.transaction():
            job = store.get(effect["job_id"])
            if job["cancel_requested"]:
                return None
            store.db.execute("UPDATE effects SET state='in_flight' WHERE id=?", (effect_id,))
        fault("before_remote")
        found = remote.create(effect["target"], json.loads(effect["request"]))
        fault("after_remote")
    with store.transaction():
        store.db.execute(
            "UPDATE effects SET state='confirmed',remote_id=? WHERE id=?", (found, effect_id)
        )
        store.event(effect["job_id"], "effect.confirmed", {"effect_id": effect_id})
    return found
