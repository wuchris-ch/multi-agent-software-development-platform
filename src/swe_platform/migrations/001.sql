CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, submission_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
 payload_sha TEXT NOT NULL, state TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
 epoch INTEGER NOT NULL DEFAULT 0, owner TEXT, lease_until REAL,
 created REAL NOT NULL, deadline REAL NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
 candidate TEXT, report TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS attempts (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), epoch INTEGER NOT NULL,
 workspace TEXT NOT NULL, created REAL NOT NULL, result TEXT,
 UNIQUE(job_id, epoch)
);
CREATE TABLE IF NOT EXISTS events (
 job_id TEXT NOT NULL REFERENCES jobs(id), seq INTEGER NOT NULL, at REAL NOT NULL,
 kind TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(job_id, seq)
);
CREATE TABLE IF NOT EXISTS effects (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), kind TEXT NOT NULL,
 target TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', request TEXT NOT NULL,
 remote_id TEXT, UNIQUE(job_id, kind, target)
);
PRAGMA user_version=1;
