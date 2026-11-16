"""SQLite 读写：一次运行的全部证据行都落库，页面只读这里。

金额一律以「分」为整数存储，避免浮点累加；大整数编号保持字符串。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, dataset TEXT, rule_version TEXT,
  created_at TEXT, status TEXT, stats TEXT, manifest TEXT
);
CREATE TABLE IF NOT EXISTS touchpoints (
  run_id TEXT, row_no INTEGER, raw_line INTEGER, touch_id TEXT,
  user_id TEXT, canonical_user TEXT, device_id TEXT, channel TEXT,
  event_type TEXT, event_time_utc TEXT, ingested_at_utc TEXT,
  cost_cents INTEGER, status TEXT, reason TEXT, late INTEGER,
  PRIMARY KEY (run_id, row_no)
);
CREATE TABLE IF NOT EXISTS orders (
  run_id TEXT, row_no INTEGER, raw_line INTEGER, order_id TEXT,
  user_id TEXT, canonical_user TEXT, gmv_cents INTEGER,
  order_time_utc TEXT, status TEXT, reason TEXT,
  PRIMARY KEY (run_id, row_no)
);
CREATE TABLE IF NOT EXISTS quarantine (
  run_id TEXT, source TEXT, row_no INTEGER, raw_line INTEGER,
  reason TEXT, raw_value TEXT,
  PRIMARY KEY (run_id, source, row_no)
);
CREATE TABLE IF NOT EXISTS credits (
  run_id TEXT, model TEXT, order_id TEXT, touch_id TEXT, channel TEXT,
  gmv_cents INTEGER, position INTEGER, late INTEGER,
  PRIMARY KEY (run_id, model, order_id, touch_id, position)
);
CREATE TABLE IF NOT EXISTS budgets (
  run_id TEXT, plan_version INTEGER, total_cents INTEGER,
  payload TEXT, created_at TEXT, is_stale INTEGER DEFAULT 0,
  PRIMARY KEY (run_id, plan_version)
);
CREATE TABLE IF NOT EXISTS logs (
  run_id TEXT, seq INTEGER, ts TEXT, level TEXT, message TEXT,
  PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_tp_run ON touchpoints(run_id, status);
CREATE INDEX IF NOT EXISTS idx_cr_run ON credits(run_id, model, channel);
"""


class Store:
    def __init__(self, db_path) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def tx(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.tx() as conn:
            conn.executescript(SCHEMA)

    # ---- 运行与批次 ----
    def save_run(self, run: dict) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs(run_id, dataset, rule_version, created_at,"
                " status, stats, manifest) VALUES (?,?,?,?,?,?,?)",
                (run["run_id"], run["dataset"], run["rule_version"], run["created_at"],
                 run["status"], json.dumps(run["stats"], ensure_ascii=False),
                 json.dumps(run["manifest"], ensure_ascii=False)))

    def get_run(self, run_id: str) -> dict | None:
        with self.tx() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return _run_row(row) if row else None

    def latest_run(self) -> dict | None:
        with self.tx() as conn:
            row = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC, run_id DESC LIMIT 1").fetchone()
        return _run_row(row) if row else None

    def list_runs(self, limit: int = 30) -> list[dict]:
        with self.tx() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC, run_id DESC LIMIT ?",
                (limit,)).fetchall()
        return [_run_row(row) for row in rows]

    def count_runs(self) -> int:
        with self.tx() as conn:
            return conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    # ---- 写入一次运行的证据 ----
    def replace_evidence(self, run_id: str, touches: list[dict], orders: list[dict],
                         quarantined: list[dict], credits: list[dict],
                         logs: list[dict]) -> None:
        with self.tx() as conn:
            for table in ("touchpoints", "orders", "quarantine", "credits", "logs"):
                conn.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
            conn.executemany(
                "INSERT INTO touchpoints VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, t["row_no"], t["raw_line"], t["touch_id"], t["user_id"],
                  t["canonical_user"], t["device_id"], t["channel"], t["event_type"],
                  t["event_time_utc"], t["ingested_at_utc"], t["cost_cents"],
                  t["status"], t["reason"], int(bool(t["late"])))
                 for t in touches])
            conn.executemany(
                "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(run_id, o["row_no"], o["raw_line"], o["order_id"], o["user_id"],
                  o["canonical_user"], o["gmv_cents"], o["order_time_utc"],
                  o["status"], o["reason"]) for o in orders])
            conn.executemany(
                "INSERT INTO quarantine VALUES (?,?,?,?,?,?)",
                [(run_id, q["source"], q["row_no"], q["raw_line"], q["reason"],
                  q["raw_value"]) for q in quarantined])
            conn.executemany(
                "INSERT INTO credits VALUES (?,?,?,?,?,?,?,?)",
                [(run_id, c["model"], c["order_id"], c["touch_id"], c["channel"],
                  c["gmv_cents"], c["position"], int(bool(c["late"]))) for c in credits])
            conn.executemany(
                "INSERT INTO logs VALUES (?,?,?,?,?)",
                [(run_id, item["seq"], item["ts"], item["level"], item["message"])
                 for item in logs])

    # ---- 读取证据 ----
    def touchpoints(self, run_id: str, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM touchpoints WHERE run_id=?"
        params: list = [run_id]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY row_no"
        with self.tx() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def touchpoint_counts(self, run_id: str) -> dict:
        with self.tx() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n, COALESCE(SUM(cost_cents),0) AS cost"
                " FROM touchpoints WHERE run_id=? GROUP BY status", (run_id,)).fetchall()
        return {row["status"]: {"n": row["n"], "cost_cents": row["cost"]} for row in rows}

    def orders(self, run_id: str) -> list[dict]:
        with self.tx() as conn:
            rows = conn.execute("SELECT * FROM orders WHERE run_id=? ORDER BY row_no",
                                (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def quarantine(self, run_id: str, code: str | None = None) -> list[dict]:
        sql = "SELECT * FROM quarantine WHERE run_id=?"
        params: list = [run_id]
        if code:
            sql += " AND reason=?"
            params.append(code)
        sql += " ORDER BY source, row_no"
        with self.tx() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def reason_counts(self, run_id: str) -> list[dict]:
        with self.tx() as conn:
            rows = conn.execute(
                "SELECT reason, COUNT(*) AS n FROM quarantine WHERE run_id=?"
                " GROUP BY reason ORDER BY n DESC, reason", (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def credits(self, run_id: str, model: str, order_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM credits WHERE run_id=? AND model=?"
        params: list = [run_id, model]
        if order_id:
            sql += " AND order_id=?"
            params.append(order_id)
        sql += " ORDER BY order_id, position, touch_id"
        with self.tx() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def channel_totals(self, run_id: str, model: str) -> list[dict]:
        with self.tx() as conn:
            rows = conn.execute(
                "SELECT channel, COUNT(*) AS n, SUM(gmv_cents) AS gmv,"
                " SUM(late) AS late_n FROM credits WHERE run_id=? AND model=?"
                " GROUP BY channel ORDER BY gmv DESC, channel", (run_id, model)).fetchall()
        return [dict(row) for row in rows]

    def model_totals(self, run_id: str) -> dict:
        with self.tx() as conn:
            rows = conn.execute(
                "SELECT model, SUM(gmv_cents) AS gmv, COUNT(*) AS n"
                " FROM credits WHERE run_id=? GROUP BY model", (run_id,)).fetchall()
        return {row["model"]: {"gmv_cents": row["gmv"] or 0, "rows": row["n"]} for row in rows}

    def order_gmv_total(self, run_id: str) -> int:
        with self.tx() as conn:
            return conn.execute(
                "SELECT COALESCE(SUM(gmv_cents),0) FROM orders WHERE run_id=? AND status='OK'",
                (run_id,)).fetchone()[0]

    # ---- 预算方案 ----
    def save_budget(self, run_id: str, plan_version: int, total_cents: int,
                    payload: dict, created_at: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO budgets(run_id, plan_version, total_cents, payload,"
                " created_at, is_stale) VALUES (?,?,?,?,?,0)",
                (run_id, plan_version, total_cents,
                 json.dumps(payload, ensure_ascii=False), created_at))

    def budgets(self, run_id: str) -> list[dict]:
        with self.tx() as conn:
            rows = conn.execute(
                "SELECT * FROM budgets WHERE run_id=? ORDER BY plan_version DESC",
                (run_id,)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def latest_budget(self, run_id: str) -> dict | None:
        rows = self.budgets(run_id)
        return rows[0] if rows else None

    def next_plan_version(self, run_id: str) -> int:
        with self.tx() as conn:
            value = conn.execute("SELECT COALESCE(MAX(plan_version),0) FROM budgets"
                                 " WHERE run_id=?", (run_id,)).fetchone()[0]
        return int(value) + 1

    # ---- 日志 ----
    def logs(self, run_id: str, after: int = 0, limit: int = 400) -> list[dict]:
        with self.tx() as conn:
            rows = conn.execute(
                "SELECT * FROM logs WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                (run_id, after, limit)).fetchall()
        return [dict(row) for row in rows]

    def log_count(self, run_id: str) -> int:
        with self.tx() as conn:
            return conn.execute("SELECT COALESCE(MAX(seq),0) FROM logs WHERE run_id=?",
                                (run_id,)).fetchone()[0]


def _run_row(row) -> dict:
    return {
        "run_id": row["run_id"],
        "dataset": row["dataset"],
        "rule_version": row["rule_version"],
        "created_at": row["created_at"],
        "status": row["status"],
        "stats": json.loads(row["stats"]),
        "manifest": json.loads(row["manifest"]),
    }
