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
            CREATE TABLE IF NOT EXISTS research_events(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,sequence_no INTEGER NOT NULL,event_type TEXT NOT NULL,stage TEXT NOT NULL,message TEXT NOT NULL,progress INTEGER NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(job_id,sequence_no),FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS idx_research_events_job_seq ON research_events(job_id,sequence_no);
            CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,client_id TEXT NOT NULL,workspace_id TEXT,title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL);
            CREATE INDEX IF NOT EXISTS idx_conversations_client_updated ON conversations(client_id,updated_at DESC);
            CREATE TABLE IF NOT EXISTS conversation_messages(id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS idx_conversation_messages_conv_created ON conversation_messages(conversation_id,created_at);
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

    def append_event(self, job_id: str, event_type: str, stage: str, message: str, progress: int, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        import uuid
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT COALESCE(MAX(sequence_no),0)+1 FROM research_events WHERE job_id=?",(job_id,)).fetchone()
            seq=int(row[0]); event_id=uuid.uuid4().hex; created=utc_now(); detail_payload=detail or {}
            c.execute("INSERT INTO research_events VALUES(?,?,?,?,?,?,?,?,?)",(event_id,job_id,seq,event_type,stage,message,max(0,min(100,progress)),json.dumps(detail_payload,ensure_ascii=False),created))
        return {"event_id":event_id,"job_id":job_id,"sequence_no":seq,"event_type":event_type,"stage":stage,"message":message,"progress":max(0,min(100,progress)),"detail":detail_payload,"created_at":created}

    def list_events(self, job_id: str, after_sequence: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
        with self._connect() as c: rows=c.execute("SELECT * FROM research_events WHERE job_id=? AND sequence_no>? ORDER BY sequence_no LIMIT ?",(job_id,max(0,after_sequence),max(1,min(limit,1000)))).fetchall()
        return [{"event_id":r["id"],"job_id":r["job_id"],"sequence_no":r["sequence_no"],"event_type":r["event_type"],"stage":r["stage"],"message":r["message"],"progress":r["progress"],"detail":json.loads(r["detail"]),"created_at":r["created_at"]} for r in rows]

    def create_conversation(self, client_id: str, workspace_id: Optional[str], title: str = "新对话") -> Dict[str, Any]:
        import uuid
        cid=uuid.uuid4().hex; now=utc_now()
        with self._connect() as c: c.execute("INSERT INTO conversations VALUES(?,?,?,?,?,?)",(cid,client_id,workspace_id,title,now,now))
        return {"conversation_id":cid,"client_id":client_id,"workspace_id":workspace_id,"title":title,"created_at":now,"updated_at":now,"message_count":0}

    def list_conversations(self, client_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as c: rows=c.execute("""SELECT c.*,COUNT(m.id) message_count FROM conversations c LEFT JOIN conversation_messages m ON m.conversation_id=c.id WHERE c.client_id=? GROUP BY c.id ORDER BY c.updated_at DESC LIMIT ?""",(client_id,max(1,min(limit,200)))).fetchall()
        return [{"conversation_id":r["id"],"client_id":r["client_id"],"workspace_id":r["workspace_id"],"title":r["title"],"created_at":r["created_at"],"updated_at":r["updated_at"],"message_count":r["message_count"]} for r in rows]

    def get_conversation(self, conversation_id: str, client_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as c: r=c.execute("SELECT * FROM conversations WHERE id=? AND client_id=?",(conversation_id,client_id)).fetchone()
        return {"conversation_id":r["id"],"client_id":r["client_id"],"workspace_id":r["workspace_id"],"title":r["title"],"created_at":r["created_at"],"updated_at":r["updated_at"]} if r else None

    def add_message(self, conversation_id: str, role: str, content: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        import uuid
        mid=uuid.uuid4().hex; now=utc_now()
        with self._connect() as c:
            c.execute("INSERT INTO conversation_messages VALUES(?,?,?,?,?,?)",(mid,conversation_id,role,content,json.dumps(payload or {},ensure_ascii=False),now))
            count=c.execute("SELECT COUNT(*) FROM conversation_messages WHERE conversation_id=?",(conversation_id,)).fetchone()[0]
            if role=="user" and count<=1:
                title=" ".join(content.split())[:40] or "新对话"
                c.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=?",(title,now,conversation_id))
            else: c.execute("UPDATE conversations SET updated_at=? WHERE id=?",(now,conversation_id))
        return {"message_id":mid,"conversation_id":conversation_id,"role":role,"content":content,"payload":payload or {},"created_at":now}

    def list_messages(self, conversation_id: str, client_id: str, limit: int = 200) -> Optional[List[Dict[str, Any]]]:
        if not self.get_conversation(conversation_id,client_id): return None
        with self._connect() as c: rows=c.execute("SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY created_at LIMIT ?",(conversation_id,max(1,min(limit,500)))).fetchall()
        return [{"message_id":r["id"],"conversation_id":r["conversation_id"],"role":r["role"],"content":r["content"],"payload":json.loads(r["payload"]),"created_at":r["created_at"]} for r in rows]

    def delete_conversation(self, conversation_id: str, client_id: str) -> bool:
        with self._connect() as c: cur=c.execute("DELETE FROM conversations WHERE id=? AND client_id=?",(conversation_id,client_id))
        return cur.rowcount>0
