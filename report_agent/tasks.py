"""SQLite-backed report task queue for local and single-host deployments."""

from __future__ import annotations

import sqlite3
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc)


class ReportTaskStore:
    def __init__(self, database_path: Path, lease_minutes: int = 30, max_attempts: int = 3):
        self.database_path = Path(database_path)
        self.lease_minutes = lease_minutes
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts 必须是正整数")
        self.max_attempts = max_attempts
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS report_tasks (
                task_id TEXT PRIMARY KEY,
                business_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed')),
                approval_status TEXT NOT NULL DEFAULT 'pending' CHECK (approval_status IN ('pending','approved')),
                attempt_count INTEGER NOT NULL DEFAULT 0,
                available_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                submitted_by TEXT NOT NULL DEFAULT 'local-cli',
                lease_owner TEXT,
                lease_until TEXT,
                markdown_path TEXT,
                manifest_path TEXT,
                published_path TEXT,
                approval_path TEXT,
                error TEXT
            )""")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(report_tasks)")}
            if "approval_status" not in columns:
                conn.execute("ALTER TABLE report_tasks ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'pending'")
            if "attempt_count" not in columns:
                conn.execute("ALTER TABLE report_tasks ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0")
            if "available_at" not in columns:
                conn.execute("ALTER TABLE report_tasks ADD COLUMN available_at TEXT")
            if "submitted_by" not in columns:
                conn.execute("ALTER TABLE report_tasks ADD COLUMN submitted_by TEXT NOT NULL DEFAULT 'local-cli'")
            if "published_path" not in columns:
                conn.execute("ALTER TABLE report_tasks ADD COLUMN published_path TEXT")
            if "approval_path" not in columns:
                conn.execute("ALTER TABLE report_tasks ADD COLUMN approval_path TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS report_tasks_queue_idx ON report_tasks(status, created_at)")
            conn.execute("""CREATE TABLE IF NOT EXISTS report_audit_events (
                event_id TEXT PRIMARY KEY,
                task_id TEXT,
                actor TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                action TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS report_audit_task_idx "
                         "ON report_audit_events(task_id, occurred_at)")

    def submit(self, business_date: str, submitted_by: str = "local-cli",
               policy_version: str = "local") -> dict:
        try:
            if date.fromisoformat(business_date).isoformat() != business_date:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError("业务日期必须为 YYYY-MM-DD") from exc
        if not isinstance(submitted_by, str) or not submitted_by or len(submitted_by) > 128:
            raise ValueError("提交人身份无效")
        if not isinstance(policy_version, str) or not policy_version or len(policy_version) > 128:
            raise ValueError("权限策略版本无效")
        task_id = uuid.uuid4().hex
        now = _now().isoformat()
        with sqlite3.connect(self.database_path, timeout=10) as conn:
            conn.execute("INSERT INTO report_tasks "
                         "(task_id,business_date,status,created_at,updated_at,submitted_by) "
                         "VALUES (?,?, 'queued', ?, ?, ?)",
                         (task_id, business_date, now, now, submitted_by))
            self._insert_audit(conn, task_id, submitted_by, policy_version, "task.submit", now,
                               {"business_date": business_date})
        return self.get(task_id)

    @staticmethod
    def _insert_audit(conn, task_id: str | None, actor: str, policy_version: str,
                      action: str, occurred_at: str, metadata: dict):
        conn.execute("INSERT INTO report_audit_events "
                     "(event_id,task_id,actor,policy_version,action,occurred_at,metadata_json) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (uuid.uuid4().hex, task_id, actor, policy_version, action,
                      occurred_at, json.dumps(metadata, ensure_ascii=False, sort_keys=True)))

    def record_audit(self, task_id: str | None, actor: str, policy_version: str,
                     action: str, metadata: dict | None = None) -> None:
        if not isinstance(actor, str) or not actor or len(actor) > 128:
            raise ValueError("审计身份无效")
        if not isinstance(policy_version, str) or not policy_version or len(policy_version) > 128:
            raise ValueError("审计策略版本无效")
        if not isinstance(action, str) or not action or len(action) > 64:
            raise ValueError("审计动作无效")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("审计元数据必须为对象")
        now = _now().isoformat()
        with sqlite3.connect(self.database_path, timeout=10) as conn:
            self._insert_audit(conn, task_id, actor, policy_version, action, now, metadata or {})

    def list_audit(self, task_id: str | None = None, limit: int = 100) -> list[dict]:
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间")
        with sqlite3.connect(self.database_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM report_audit_events "
                                "WHERE (? IS NULL OR task_id=?) "
                                "ORDER BY occurred_at DESC, event_id DESC LIMIT ?",
                                (task_id, task_id, limit)).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["metadata"] = json.loads(event.pop("metadata_json"))
            events.append(event)
        return events

    def get(self, task_id: str, requester: str | None = None) -> dict:
        with sqlite3.connect(self.database_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM report_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError("任务不存在")
        result = dict(row)
        if requester is not None and result["submitted_by"] != requester:
            raise KeyError("任务不存在")
        return result

    def list_pending_approval(self, limit: int = 50) -> list[dict]:
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间")
        with sqlite3.connect(self.database_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM report_tasks WHERE status='completed' "
                                "AND approval_status='pending' ORDER BY updated_at, task_id LIMIT ?",
                                (limit,)).fetchall()
        return [dict(row) for row in rows]

    def claim(self, worker_id: str) -> dict | None:
        now = _now()
        lease_until = (now + timedelta(minutes=self.lease_minutes)).isoformat()
        with sqlite3.connect(self.database_path, timeout=10, isolation_level=None) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT task_id FROM report_tasks WHERE status='queued' "
                               "AND (available_at IS NULL OR available_at<=?) "
                               "ORDER BY created_at, task_id LIMIT 1", (now.isoformat(),)).fetchone()
            if row is None:
                conn.commit()
                return None
            task_id = row["task_id"]
            changed = conn.execute("UPDATE report_tasks SET status='running', attempt_count=attempt_count+1, "
                                    "lease_owner=?, lease_until=?, "
                                    "updated_at=?, error=NULL WHERE task_id=? AND status='queued'",
                                    (worker_id, lease_until, now.isoformat(), task_id)).rowcount
            if not changed:
                conn.rollback()
                return None
            conn.commit()
        return self.get(task_id)

    def recover_expired(self) -> int:
        now_dt = _now()
        now = now_dt.isoformat()
        recovered = 0
        with sqlite3.connect(self.database_path, timeout=10, isolation_level=None) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            expired = conn.execute("SELECT task_id, attempt_count FROM report_tasks "
                                   "WHERE status='running' AND lease_until < ?", (now,)).fetchall()
            for task in expired:
                if task["attempt_count"] < self.max_attempts:
                    delay = min(2 ** max(0, task["attempt_count"] - 1), 60)
                    available_at = (now_dt + timedelta(seconds=delay)).isoformat()
                    error = "worker lease expired; task requeued"
                    status = "queued"
                    recovered += 1
                else:
                    available_at = None
                    error = "worker lease expired; retry limit reached"
                    status = "failed"
                conn.execute("UPDATE report_tasks SET status=?, lease_owner=NULL, lease_until=NULL, "
                             "available_at=?, updated_at=?, error=? WHERE task_id=? AND status='running'",
                             (status, available_at, now, error, task["task_id"]))
            conn.commit()
        return recovered

    def complete(self, task_id: str, worker_id: str, markdown_path: Path, manifest_path: Path):
        now = _now().isoformat()
        with sqlite3.connect(self.database_path, timeout=10) as conn:
            cursor = conn.execute("UPDATE report_tasks SET status='completed', updated_at=?, lease_owner=NULL, "
                                  "lease_until=NULL, markdown_path=?, manifest_path=?, error=NULL "
                                  "WHERE task_id=? AND status='running' AND lease_owner=? AND lease_until>=?",
                                  (now, str(markdown_path), str(manifest_path), task_id, worker_id, now))
            if cursor.rowcount != 1:
                raise RuntimeError("任务租约已失效，拒绝提交迟到结果")

    def renew(self, task_id: str, worker_id: str) -> None:
        now = _now()
        lease_until = (now + timedelta(minutes=self.lease_minutes)).isoformat()
        with sqlite3.connect(self.database_path, timeout=10) as conn:
            cursor = conn.execute("UPDATE report_tasks SET lease_until=?, updated_at=? "
                                  "WHERE task_id=? AND status='running' AND lease_owner=? AND lease_until>=?",
                                  (lease_until, now.isoformat(), task_id, worker_id, now.isoformat()))
            if cursor.rowcount != 1:
                raise RuntimeError("任务租约已失效，拒绝继续生成")

    def mark_approved(self, task_id: str, published_path: Path, approval_path: Path) -> None:
        now = _now().isoformat()
        with sqlite3.connect(self.database_path, timeout=10) as conn:
            cursor = conn.execute("UPDATE report_tasks SET approval_status='approved', updated_at=?, "
                                  "published_path=?, approval_path=? "
                                  "WHERE task_id=? AND status='completed' AND approval_status='pending'",
                                  (now, str(published_path), str(approval_path), task_id))
            if cursor.rowcount != 1:
                raise RuntimeError("任务未完成或已审批，无法更新审批状态")

    def fail(self, task_id: str, worker_id: str, error: str):
        now = _now().isoformat()
        with sqlite3.connect(self.database_path, timeout=10) as conn:
            cursor = conn.execute("UPDATE report_tasks SET status='failed', updated_at=?, lease_owner=NULL, "
                                  "lease_until=NULL, error=? WHERE task_id=? AND status='running' AND lease_owner=?",
                                  (now, error[:500], task_id, worker_id))
            if cursor.rowcount != 1:
                raise RuntimeError("任务租约已失效，拒绝覆盖新状态")

    def retry_or_fail(self, task_id: str, worker_id: str, error: str, retryable: bool) -> str:
        now = _now()
        with sqlite3.connect(self.database_path, timeout=10, isolation_level=None) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT attempt_count FROM report_tasks WHERE task_id=? "
                               "AND status='running' AND lease_owner=?", (task_id, worker_id)).fetchone()
            if row is None:
                conn.rollback()
                raise RuntimeError("任务租约已失效，拒绝更新重试状态")
            can_retry = retryable and row["attempt_count"] < self.max_attempts
            if can_retry:
                delay = min(2 ** max(0, row["attempt_count"] - 1), 60)
                available_at = (now + timedelta(seconds=delay)).isoformat()
                status = "queued"
            else:
                available_at = None
                status = "failed"
            cursor = conn.execute("UPDATE report_tasks SET status=?, available_at=?, updated_at=?, "
                                  "lease_owner=NULL, lease_until=NULL, error=? "
                                  "WHERE task_id=? AND status='running' AND lease_owner=?",
                                  (status, available_at, now.isoformat(), error[:500], task_id, worker_id))
            if cursor.rowcount != 1:
                conn.rollback()
                raise RuntimeError("任务租约已失效，拒绝更新重试状态")
            conn.commit()
            return status
