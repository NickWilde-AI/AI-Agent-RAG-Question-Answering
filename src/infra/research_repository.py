"""SQLite 研究仓库：Redis 可做缓存，但 SQLite 是默认真源。"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..research_models import utc_now


class ResearchRepository:
    def create_workspace(self, name: str, description: str, use_demo: bool) -> Dict[str, Any]: raise NotImplementedError
    def get_workspace(self, workspace_id: str) -> Optional[Dict[str, Any]]: raise NotImplementedError


class SQLiteResearchRepository(ResearchRepository):
    def __init__(self, path: str = "data/research/research.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self) -> None:
        with self._connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,use_demo INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,file_name TEXT NOT NULL,source_path TEXT NOT NULL,content_type TEXT NOT NULL,status TEXT NOT NULL,page_count INTEGER NOT NULL DEFAULT 0,error_message TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS pages(page_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,document_id TEXT NOT NULL,payload TEXT NOT NULL,FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,idempotency_key TEXT,created_at TEXT NOT NULL,UNIQUE(workspace_id,idempotency_key),FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY,job_id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE);
            """)
            c.execute("UPDATE jobs SET status='pending', payload=json_set(payload,'$.status','pending','$.error_message','interrupted by service restart') WHERE status IN ('planning','running','verifying')")

    def create_workspace(self, name: str, description: str, use_demo: bool) -> Dict[str, Any]:
        import uuid
        now, wid = utc_now(), uuid.uuid4().hex
        with self._connect() as c: c.execute("INSERT INTO workspaces VALUES(?,?,?,?,?,?)", (wid,name,description,int(use_demo),now,now))
        return self.get_workspace(wid) or {}

    def list_workspaces(self) -> List[Dict[str, Any]]:
        with self._connect() as c: rows=c.execute("SELECT w.*,COUNT(d.id) document_count FROM workspaces w LEFT JOIN documents d ON d.workspace_id=w.id GROUP BY w.id ORDER BY w.created_at DESC").fetchall()
        return [self._workspace(r) for r in rows]

    def _workspace(self, r: sqlite3.Row) -> Dict[str, Any]:
        return {"workspace_id":r["id"],"name":r["name"],"description":r["description"],"use_demo":bool(r["use_demo"]),"created_at":r["created_at"],"updated_at":r["updated_at"],"document_count":int(r["document_count"]) if "document_count" in r.keys() else len(self.list_documents(r["id"]))}

    def get_workspace(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as c: r=c.execute("SELECT * FROM workspaces WHERE id=?",(workspace_id,)).fetchone()
        return self._workspace(r) if r else None

    def delete_workspace(self, workspace_id: str) -> bool:
        with self._connect() as c: cur=c.execute("DELETE FROM workspaces WHERE id=?",(workspace_id,))
        return cur.rowcount > 0

    def has_active_jobs(self, workspace_id: str) -> bool:
        with self._connect() as c:
            row = c.execute(
                "SELECT 1 FROM jobs WHERE workspace_id=? AND status IN ('pending','planning','running','verifying') LIMIT 1",
                (workspace_id,),
            ).fetchone()
        return row is not None

    def add_document(self, document: Dict[str, Any], page_payloads: List[Dict[str, Any]]) -> None:
        with self._connect() as c:
            c.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)", tuple(document[k] for k in ("document_id","workspace_id","file_name","source_path","content_type","status","page_count","error_message","created_at","updated_at")))
            c.executemany("INSERT INTO pages VALUES(?,?,?,?)",[(p["page_id"],document["workspace_id"],document["document_id"],json.dumps(p,ensure_ascii=False)) for p in page_payloads])
            c.execute("UPDATE workspaces SET updated_at=? WHERE id=?", (utc_now(), document["workspace_id"]))

    def list_documents(self, workspace_id: str) -> List[Dict[str, Any]]:
        with self._connect() as c: rows=c.execute("SELECT * FROM documents WHERE workspace_id=? ORDER BY created_at",(workspace_id,)).fetchall()
        return [{"document_id":r["id"],"workspace_id":r["workspace_id"],"file_name":r["file_name"],"source_path":r["source_path"],"content_type":r["content_type"],"status":r["status"],"page_count":r["page_count"],"error_message":r["error_message"],"created_at":r["created_at"],"updated_at":r["updated_at"]} for r in rows]

    def delete_document(self, workspace_id: str, document_id: str) -> Optional[str]:
        with self._connect() as c:
            r=c.execute("SELECT source_path FROM documents WHERE id=? AND workspace_id=?",(document_id,workspace_id)).fetchone()
            if not r: return None
            c.execute("DELETE FROM pages WHERE document_id=?",(document_id,)); c.execute("DELETE FROM documents WHERE id=?",(document_id,))
            c.execute("UPDATE workspaces SET updated_at=? WHERE id=?", (utc_now(), workspace_id))
        return str(r["source_path"])

    def list_pages(self, workspace_id: str) -> List[Dict[str, Any]]:
        with self._connect() as c: rows=c.execute("SELECT payload FROM pages WHERE workspace_id=?",(workspace_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def create_job(self, payload: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        """原子创建幂等任务；并发相同 key 时返回先创建的任务。"""
        values=(payload["job_id"],payload["workspace_id"],json.dumps(payload,ensure_ascii=False),payload["status"],payload.get("idempotency_key"),payload["created_at"])
        with self._lock, self._connect() as c:
            try:
                c.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)", values)
                return payload, True
            except sqlite3.IntegrityError:
                key=payload.get("idempotency_key")
                if not key: raise
                row=c.execute("SELECT payload FROM jobs WHERE workspace_id=? AND idempotency_key=?",(payload["workspace_id"],key)).fetchone()
                if not row: raise
                return json.loads(row[0]), False

    def save_job(self, payload: Dict[str, Any], preserve_cancelled: bool = True) -> bool:
        values=(payload["job_id"],payload["workspace_id"],json.dumps(payload,ensure_ascii=False),payload["status"],payload.get("idempotency_key"),payload["created_at"])
        where = " WHERE jobs.status != 'cancelled' OR excluded.status = 'cancelled'" if preserve_cancelled else ""
        with self._lock, self._connect() as c:
            cur=c.execute("""INSERT INTO jobs VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,status=excluded.status,idempotency_key=excluded.idempotency_key"""+where,values)
        return cur.rowcount > 0

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as c:r=c.execute("SELECT payload FROM jobs WHERE id=?",(job_id,)).fetchone()
        return json.loads(r[0]) if r else None

    def get_job_by_key(self, workspace_id: str, key: str) -> Optional[Dict[str, Any]]:
        with self._connect() as c:r=c.execute("SELECT payload FROM jobs WHERE workspace_id=? AND idempotency_key=?",(workspace_id,key)).fetchone()
        return json.loads(r[0]) if r else None

    def request_cancel(self, job_id: str) -> bool:
        with self._lock, self._connect() as c:
            row=c.execute("SELECT payload,status FROM jobs WHERE id=?",(job_id,)).fetchone()
            if not row or row["status"] not in ("pending","planning","running","verifying"): return False
            job=json.loads(row["payload"]); job["status"]="cancelled"; job["finished_at"]=utc_now()
            cur=c.execute("UPDATE jobs SET payload=?,status='cancelled' WHERE id=? AND status IN ('pending','planning','running','verifying')",(json.dumps(job,ensure_ascii=False),job_id))
        return cur.rowcount > 0

    def save_report(self, payload: Dict[str, Any]) -> None:
        with self._connect() as c:c.execute("""INSERT INTO reports VALUES(?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET id=excluded.id,payload=excluded.payload,created_at=excluded.created_at""",(payload["report_id"],payload["job_id"],json.dumps(payload,ensure_ascii=False),payload["created_at"]))

    def get_report(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as c:r=c.execute("SELECT payload FROM reports WHERE job_id=?",(job_id,)).fetchone()
        return json.loads(r[0]) if r else None
