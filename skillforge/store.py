"""ExperimentStore — SQLite persistence of everything needed to reproduce a run.

Plain SQL with ``?`` parameters and JSON text columns only, so the schema ports
to PostgreSQL by swapping the connection (``?`` -> ``%s``, TEXT -> JSONB).
The holdout split is protected by a one-way ``holdout_opened`` flag.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, created_at REAL, request TEXT, status TEXT, seed INTEGER, provider TEXT,
  spec_json TEXT, config_json TEXT, env_json TEXT, summary_json TEXT, holdout_opened INTEGER DEFAULT 0,
  error TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY, run_id TEXT, generation INTEGER, parents_json TEXT, label TEXT, architecture TEXT,
  fingerprint TEXT, origin TEXT, ir_json TEXT, mutation_log_json TEXT, complexity REAL, created_at REAL,
  status TEXT DEFAULT 'evaluated', rejection_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_cand_run ON candidates(run_id);
CREATE TABLE IF NOT EXISTS evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, candidate_id TEXT, split TEXT, generation INTEGER,
  feasible INTEGER, pass_rate REAL, cost_per_item REAL, summary_json TEXT, created_at REAL
);
CREATE INDEX IF NOT EXISTS ix_eval_run ON evaluations(run_id, split);
CREATE TABLE IF NOT EXISTS scenarios (
  run_id TEXT, id TEXT, split TEXT, family TEXT, origin TEXT, realism TEXT, seed TEXT, data_json TEXT,
  PRIMARY KEY (run_id, id)
);
CREATE TABLE IF NOT EXISTS generations (run_id TEXT, generation INTEGER, stats_json TEXT, PRIMARY KEY (run_id, generation));
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL, kind TEXT,
  message TEXT, data_json TEXT);
CREATE INDEX IF NOT EXISTS ix_events_run ON events(run_id);
CREATE TABLE IF NOT EXISTS holdout_attempts (run_id TEXT, attempt INTEGER, commitment TEXT, opened_at REAL,
  candidate_id TEXT, passed INTEGER, summary_json TEXT, PRIMARY KEY (run_id, attempt));
CREATE TABLE IF NOT EXISTS artifacts (run_id TEXT, path TEXT, sha256 TEXT, kind TEXT, PRIMARY KEY (run_id, path));
"""


class HoldoutAlreadyOpened(RuntimeError):
    pass


def _j(x: Any) -> str:
    return json.dumps(x, default=str, sort_keys=True)


class ExperimentStore:
    def __init__(self, path: str | Path = "skillforge.db"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL") if self.path != ":memory:" else None
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, args)

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    # ---------------- writes
    def create_run(self, run_id: str, request: str, seed: int, provider: str, config: dict, env: dict) -> None:
        self._exec("INSERT INTO runs (id, created_at, request, status, seed, provider, config_json, env_json) "
                   "VALUES (?,?,?,?,?,?,?,?)", (run_id, time.time(), request, "running", seed, provider, _j(config),
                                                _j(env)))

    def update_run(self, run_id: str, **fields: Any) -> None:
        cols, vals = [], []
        for k, v in fields.items():
            if k in ("spec", "summary"):
                k, v = f"{k}_json", _j(v)
            cols.append(f"{k}=?")
            vals.append(v)
        self._exec(f"UPDATE runs SET {', '.join(cols)} WHERE id=?", (*vals, run_id))

    def open_holdout(self, run_id: str, attempt: int = 0, commitment: str = "", candidate_id: str = "") -> None:
        """One-way lock: each holdout set (attempt) can be opened exactly once."""
        with self._lock:
            try:
                self._conn.execute("INSERT INTO holdout_attempts (run_id, attempt, commitment, opened_at, candidate_id) "
                                   "VALUES (?,?,?,?,?)", (run_id, attempt, commitment, time.time(), candidate_id))
            except sqlite3.IntegrityError as e:
                raise HoldoutAlreadyOpened(f"holdout #{attempt} of run {run_id} was already evaluated") from e
            self._conn.execute("UPDATE runs SET holdout_opened=holdout_opened+1 WHERE id=?", (run_id,))

    def close_holdout(self, run_id: str, attempt: int, passed: bool, summary: dict) -> None:
        self._exec("UPDATE holdout_attempts SET passed=?, summary_json=? WHERE run_id=? AND attempt=?",
                   (int(passed), _j(summary), run_id, attempt))

    def holdout_attempts(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM holdout_attempts WHERE run_id=? ORDER BY attempt", (run_id,))
        for r in rows:
            r["summary"] = json.loads(r.pop("summary_json") or "null")
        return rows

    def save_candidate(self, run_id: str, ir, status: str = "evaluated", reason: str | None = None) -> None:
        self._exec(
            "INSERT INTO candidates (id, run_id, generation, parents_json, label, architecture, fingerprint, origin, "
            "ir_json, mutation_log_json, complexity, created_at, status, rejection_reason) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
            "rejection_reason=COALESCE(excluded.rejection_reason, candidates.rejection_reason)",
            (ir.id, run_id, ir.generation, _j(ir.parents), ir.label, ir.architecture().value, ir.fingerprint(),
             ir.origin, ir.model_dump_json(), _j(ir.mutation_log), ir.complexity(), time.time(), status, reason))

    def set_candidate_status(self, cand_id: str, status: str, reason: str | None = None) -> None:
        self._exec("UPDATE candidates SET status=?, rejection_reason=COALESCE(?, rejection_reason) WHERE id=?",
                   (status, reason, cand_id))

    def save_evaluation(self, run_id: str, generation: int, result) -> None:
        s = result.summary()
        self._exec("INSERT INTO evaluations (run_id, candidate_id, split, generation, feasible, pass_rate, "
                   "cost_per_item, summary_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                   (run_id, result.candidate_id, result.split, generation, int(result.feasible),
                    result.metrics["pass_rate"], result.metrics["cost_per_item_usd"], _j(s), time.time()))

    def save_scenarios(self, run_id: str, split: str, scenarios) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO scenarios VALUES (?,?,?,?,?,?,?,?)",
                [(run_id, s.id, split, s.family, s.origin, s.realism, str(s.seed), _j(s.to_dict())) for s in scenarios])

    def save_generation(self, run_id: str, gen: int, stats: dict) -> None:
        self._exec("INSERT OR REPLACE INTO generations VALUES (?,?,?)", (run_id, gen, _j(stats)))

    def event(self, run_id: str, kind: str, message: str, data: Any = None) -> None:
        self._exec("INSERT INTO events (run_id, ts, kind, message, data_json) VALUES (?,?,?,?,?)",
                   (run_id, time.time(), kind, message, _j(data or {})))

    def save_artifact(self, run_id: str, path: str, sha: str, kind: str) -> None:
        self._exec("INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?)", (run_id, path, sha, kind))

    # ---------------- reads
    def runs(self) -> list[dict[str, Any]]:
        rows = self._all("SELECT id, created_at, request, status, seed, provider, holdout_opened, error, summary_json "
                         "FROM runs ORDER BY created_at DESC")
        for r in rows:
            r["summary"] = json.loads(r.pop("summary_json") or "null")
        return rows

    def run(self, run_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM runs WHERE id=?", (run_id,))
        if not rows:
            return None
        r = rows[0]
        for k in ("spec_json", "config_json", "env_json", "summary_json"):
            r[k[:-5]] = json.loads(r.pop(k) or "null")
        return r

    def candidates(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._all("SELECT id, generation, parents_json, label, architecture, fingerprint, origin, "
                         "mutation_log_json, complexity, status, rejection_reason FROM candidates WHERE run_id=? "
                         "ORDER BY generation, created_at", (run_id,))
        for r in rows:
            r["parents"] = json.loads(r.pop("parents_json"))
            r["mutation_log"] = json.loads(r.pop("mutation_log_json"))
        return rows

    def candidate(self, cand_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM candidates WHERE id=?", (cand_id,))
        if not rows:
            return None
        r = rows[0]
        r["ir"] = json.loads(r.pop("ir_json"))
        r["parents"] = json.loads(r.pop("parents_json"))
        r["mutation_log"] = json.loads(r.pop("mutation_log_json"))
        r["evaluations"] = [dict(e, summary=json.loads(e.pop("summary_json"))) for e in
                            self._all("SELECT * FROM evaluations WHERE candidate_id=? ORDER BY id", (cand_id,))]
        return r

    def evaluations(self, run_id: str, split: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM evaluations WHERE run_id=?" + (" AND split=?" if split else "") + " ORDER BY id"
        rows = self._all(q, (run_id, split) if split else (run_id,))
        for r in rows:
            r["summary"] = json.loads(r.pop("summary_json"))
        return rows

    def generations(self, run_id: str) -> list[dict[str, Any]]:
        return [json.loads(r["stats_json"]) for r in
                self._all("SELECT stats_json FROM generations WHERE run_id=? ORDER BY generation", (run_id,))]

    def events(self, run_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id", (run_id, after_id))
        for r in rows:
            r["data"] = json.loads(r.pop("data_json"))
        return rows

    def scenario_counts(self, run_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT split, family, origin, realism, COUNT(*) AS n FROM scenarios WHERE run_id=? "
                         "GROUP BY split, family, origin, realism ORDER BY split, family", (run_id,))

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM artifacts WHERE run_id=? ORDER BY path", (run_id,))
