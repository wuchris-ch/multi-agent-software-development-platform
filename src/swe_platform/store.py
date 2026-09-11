import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .io import canonical, digest
from .models import EDGES, TERMINAL, Conflict, Fenced, State, Submission


class Store:
    """The service owns this connection. Every mutation uses one short transaction."""

    def __init__(self, root: Path, clock=time.time):
        self.root = root.resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        self.clock = clock
        self.db = sqlite3.connect(root / "state.sqlite", timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            raise ValueError("Database was created by a newer platform")
        if version == 0:
            sql = (Path(__file__).parent / "migrations/001.sql").read_text()
            self.db.executescript("BEGIN IMMEDIATE;\n" + sql + "\nCOMMIT;")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def get(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown job")
        return dict(row)

    def jobs(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY created")]

    def event(self, job_id, kind, data):
        self.db.execute(
            "INSERT INTO events SELECT ?,COALESCE(MAX(seq),0)+1,?,?,? FROM events WHERE job_id=?",
            (job_id, self.clock(), kind, canonical(data).decode(), job_id),
        )

    def submit(self, spec: Submission):
        payload = canonical(spec.model_dump()).decode()
        sha = digest(payload.encode())
        with self.transaction():
            old = self.db.execute(
                "SELECT * FROM jobs WHERE submission_key=?", (spec.key,)
            ).fetchone()
            if old:
                if old["payload_sha"] != sha:
                    raise Conflict("Submission key already has a different payload")
                return dict(old)
            job_id = str(uuid.uuid4())
            self.db.execute(
                "INSERT INTO jobs(id,submission_key,payload,payload_sha,state,created,deadline) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    job_id,
                    spec.key,
                    payload,
                    sha,
                    State.queued,
                    self.clock(),
                    self.clock() + spec.deadline_seconds,
                ),
            )
            self.event(job_id, "job.created", {"schema_version": "1.0"})
        return self.get(job_id)

    def _check(self, row, version, owner=None, epoch=None):
        if row["version"] != version:
            raise Conflict("Job version changed")
        if owner is not None and (
            row["owner"] != owner or row["epoch"] != epoch or row["lease_until"] <= self.clock()
        ):
            raise Fenced("Stale or expired execution ownership")

    def _transition(self, row, target, effect=None, **fields):
        source = State(row["state"])
        target = State(target)
        allowed = set(EDGES.get(source, set()))
        if source not in TERMINAL and source != State.cancelling:
            allowed |= {State.cancelling, State.needs_attention, State.failed_infra}
        if target not in allowed:
            raise Conflict(f"Illegal transition: {source} -> {target}")
        if set(fields) - {"candidate", "report", "error", "cancel_requested"}:
            raise ValueError("Unsupported state field")
        assignments = ",".join(f"{k}=?" for k in fields)
        self.db.execute(
            "UPDATE jobs SET state=?,version=version+1"
            + ("," + assignments if fields else "")
            + " WHERE id=? AND version=?",
            (target, *fields.values(), row["id"], row["version"]),
        )
        self.event(row["id"], "state.changed", {"from": source, "to": target, **fields})
        if effect:
            kind, effect_target, request = effect
            self.db.execute(
                "INSERT INTO effects(id,job_id,kind,target,request) VALUES(?,?,?,?,?)",
                (str(uuid.uuid4()), row["id"], kind, effect_target, canonical(request).decode()),
            )

    def transition(self, job_id, version, target, *, owner=None, epoch=None, effect=None, **fields):
        with self.transaction():
            row = self.get(job_id)
            self._check(row, version, owner, epoch)
            self._transition(row, target, effect, **fields)
        return self.get(job_id)

    def launch_intent(self, job_id, owner):
        with self.transaction():
            row = self.get(job_id)
            if row["state"] != State.queued:
                raise Conflict("Job is not queued")
            epoch = row["epoch"] + 1
            attempt = str(uuid.uuid4())
            path = str(self.root / "executions" / attempt)
            self.db.execute(
                "UPDATE jobs SET owner=?,epoch=?,lease_until=? WHERE id=?",
                (owner, epoch, self.clock() + 30, job_id),
            )
            self.db.execute(
                "INSERT INTO attempts VALUES(?,?,?,?,?,NULL)",
                (attempt, job_id, epoch, path, self.clock()),
            )
            self._transition(row, State.implementing, ("execute", attempt, {"epoch": epoch}))
        return self.attempt(job_id)

    def adopt(self, job_id, owner):
        # A new service adopts the SAME execution epoch under the exclusive service lock.
        # Replacements (not yet exposed) must increment epoch and use a fresh workspace.
        with self.transaction():
            self.db.execute(
                "UPDATE jobs SET owner=?,lease_until=? WHERE id=?",
                (owner, self.clock() + 30, job_id),
            )

    def attempt(self, job_id):
        row = self.db.execute(
            "SELECT * FROM attempts WHERE job_id=? ORDER BY epoch DESC LIMIT 1", (job_id,)
        ).fetchone()
        return dict(row) if row else None

    def complete(self, job_id, owner, completion):
        with self.transaction():
            row = self.get(job_id)
            self._check(row, row["version"], owner, completion.epoch)
            attempt = self.attempt(job_id)
            if attempt["id"] != completion.attempt_id:
                raise Fenced("Completion belongs to another attempt")
            self.db.execute(
                "UPDATE attempts SET result=? WHERE id=?",
                (completion.model_dump_json(), attempt["id"]),
            )
            self.db.execute(
                "UPDATE effects SET state='confirmed',remote_id=? "
                "WHERE job_id=? AND kind='execute' AND target=?",
                (attempt["id"], job_id, attempt["id"]),
            )
            if completion.outcome == "completed":
                self._transition(row, State.verifying)
            else:
                self._transition(row, State.needs_attention, error=completion.outcome)

    def cancel(self, job_id):
        row = self.get(job_id)
        if row["state"] in TERMINAL or row["state"] == State.cancelling:
            return row
        return self.transition(job_id, row["version"], State.cancelling, cancel_requested=1)

    def inspect(self, job_id):
        result = self.get(job_id)
        for table in ("events", "attempts", "effects"):
            result[table] = [
                dict(r)
                for r in self.db.execute(
                    f"SELECT * FROM {table} WHERE job_id=? ORDER BY rowid", (job_id,)
                )
            ]
        result["cost"] = {"known_usd": None, "completeness": "not_metered"}
        return result
