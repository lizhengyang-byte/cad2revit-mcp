"""SQLite task store.

Only one process writes to a workspace in the MVP, so plain sqlite3 with
WAL journaling is enough.  Every write is wrapped in a transaction to keep
task state and created keys consistent.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  file_name TEXT NOT NULL,
  file_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  rule_set_version TEXT NOT NULL,
  ir_path TEXT,
  report_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ambiguities (
  task_id TEXT NOT NULL,
  element_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL,
  decision TEXT,
  decided_by TEXT,
  decided_at TEXT,
  PRIMARY KEY (task_id, element_id)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  batch_no INTEGER NOT NULL,
  instruction_hash TEXT NOT NULL,
  success INTEGER NOT NULL,
  failed INTEGER NOT NULL,
  detail TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS created_keys (
  task_id TEXT NOT NULL,
  idem_key TEXT NOT NULL,
  element_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (task_id, idem_key)
);
CREATE TABLE IF NOT EXISTS rule_versions (
  version TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


class TaskStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- tasks -----------------------------------------------------------
    def create_task(self, task_id: str, file_name: str, file_hash: str, trace_id: str,
                    rule_set_version: str, created_at: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO tasks(task_id, file_name, file_hash, status,"
                    " trace_id, rule_set_version, created_at, updated_at) VALUES"
                    " (?,?,?,?,?,?,?,?)",
                    (task_id, file_name, file_hash, "parsed", trace_id, rule_set_version,
                     created_at, created_at),
                )

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT task_id, file_name, file_hash, status, trace_id, rule_set_version,"
            " ir_path, report_path FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            return None
        keys = ["task_id", "file_name", "file_hash", "status", "trace_id",
                "rule_set_version", "ir_path", "report_path"]
        return dict(zip(keys, row))

    def update_task(self, task_id: str, updated_at: str, **fields: Any) -> None:
        if not fields:
            return
        columns = ", ".join(f"{name}=?" for name in fields)
        with self._lock:
            with self._conn:
                self._conn.execute(
                    f"UPDATE tasks SET {columns}, updated_at=? WHERE task_id=?",
                    (*fields.values(), updated_at, task_id),
                )

    # -- ambiguities -----------------------------------------------------
    def put_ambiguity(self, task_id: str, element_id: str, kind: str, detail: Dict[str, Any]) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO ambiguities(task_id, element_id, kind, detail)"
                    " VALUES (?,?,?,?)",
                    (task_id, element_id, kind, json.dumps(detail, ensure_ascii=False)),
                )

    def resolve(self, task_id: str, element_id: str, decision: str, decided_by: str,
                decided_at: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE ambiguities SET decision=?, decided_by=?, decided_at=?"
                    " WHERE task_id=? AND element_id=?",
                    (decision, decided_by, decided_at, task_id, element_id),
                )

    def list_ambiguities(self, task_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT element_id, kind, detail, decision FROM ambiguities WHERE task_id=?",
            (task_id,),
        ).fetchall()
        return [
            {"element_id": r[0], "kind": r[1], "detail": json.loads(r[2]), "decision": r[3]}
            for r in rows
        ]

    # -- runs and idempotency --------------------------------------------
    def record_run(self, task_id: str, batch_no: int, instruction_hash: str,
                   success: int, failed: int, detail: Dict[str, Any], created_at: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO runs(task_id, batch_no, instruction_hash, success, failed,"
                    " detail, created_at) VALUES (?,?,?,?,?,?,?)",
                    (task_id, batch_no, instruction_hash, success, failed,
                     json.dumps(detail, ensure_ascii=False), created_at),
                )

    def list_runs(self, task_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT batch_no, success, failed, detail FROM runs WHERE task_id=? ORDER BY id",
            (task_id,),
        ).fetchall()
        return [
            {"batch_no": r[0], "success": r[1], "failed": r[2],
             "detail": json.loads(r[3])}
            for r in rows
        ]

    def has_key(self, task_id: str, idem_key: str) -> bool:
        return bool(
            self._conn.execute(
                "SELECT 1 FROM created_keys WHERE task_id=? AND idem_key=?",
                (task_id, idem_key),
            ).fetchone()
        )

    def add_keys(self, task_id: str, keys: Iterable[tuple[str, str]], created_at: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO created_keys(task_id, idem_key, element_id, created_at)"
                    " VALUES (?,?,?,?)",
                    [(task_id, key, element_id, created_at) for key, element_id in keys],
                )

    def count_keys(self, task_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM created_keys WHERE task_id=?", (task_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    # -- rules -----------------------------------------------------------
    def register_rule_version(self, version: str, path: str, created_at: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO rule_versions(version, path, created_at) VALUES (?,?,?)",
                    (version, path, created_at),
                )
