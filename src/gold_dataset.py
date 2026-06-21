"""金标候选、人工审核状态与版本导出的持久化模型。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ALLOWED_BRANCHES = {"fact_qa", "chart_qa", "multi_page_qa"}
ALLOWED_STATUSES = {"pending", "accepted", "rejected", "skipped"}


def candidate_id(query: str, gold_pages: Iterable[str]) -> str:
    raw = "|".join(sorted(str(x) for x in gold_pages)) + "|" + " ".join(query.lower().split())
    return "gold_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


@dataclass
class GoldReviewStore:
    path: str

    def __post_init__(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidates(
                  id TEXT PRIMARY KEY, query TEXT NOT NULL, gold_answer TEXT NOT NULL,
                  gold_pages TEXT NOT NULL, gold_branch TEXT NOT NULL, category TEXT NOT NULL,
                  source_files TEXT NOT NULL, page_nos TEXT NOT NULL, image_paths TEXT NOT NULL,
                  model_verified INTEGER NOT NULL DEFAULT 0, model_reason TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'pending', reviewer_note TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS page_runs(
                  run_key TEXT PRIMARY KEY, state TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def upsert_candidate(self, row: Dict[str, Any]) -> str:
        pages = [str(x) for x in row.get("gold_pages") or []]
        cid = str(row.get("id") or candidate_id(str(row.get("query", "")), pages))
        branch = str(row.get("gold_branch") or "fact_qa")
        if branch not in ALLOWED_BRANCHES:
            raise ValueError(f"unsupported gold_branch: {branch}")
        now = self._now()
        values = (
            cid, str(row.get("query", "")).strip(), str(row.get("gold_answer", "")).strip(),
            json.dumps(pages, ensure_ascii=False), branch, str(row.get("category") or branch),
            json.dumps(row.get("source_files") or [], ensure_ascii=False),
            json.dumps(row.get("page_nos") or [], ensure_ascii=False),
            json.dumps(row.get("image_paths") or [], ensure_ascii=False),
            int(bool(row.get("model_verified"))), str(row.get("model_reason") or ""), now, now,
        )
        if not values[1] or not values[2] or not pages:
            raise ValueError("query, gold_answer and gold_pages are required")
        with self._lock, self._connect() as db:
            db.execute(
                """INSERT INTO candidates(
                id,query,gold_answer,gold_pages,gold_branch,category,source_files,page_nos,image_paths,
                model_verified,model_reason,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  model_verified=excluded.model_verified, model_reason=excluded.model_reason,
                  updated_at=excluded.updated_at
                WHERE candidates.status='pending'""", values,
            )
        return cid

    def mark_run(self, run_key: str, state: str, error: str = "") -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO page_runs VALUES(?,?,?,?) ON CONFLICT(run_key) DO UPDATE SET state=excluded.state,error=excluded.error,updated_at=excluded.updated_at",
                (run_key, state, error[:1000], self._now()),
            )

    def completed_runs(self) -> set:
        with self._connect() as db:
            return {str(r[0]) for r in db.execute("SELECT run_key FROM page_runs WHERE state='completed'")}

    @staticmethod
    def _decode(row: sqlite3.Row) -> Dict[str, Any]:
        out = dict(row)
        for key in ("gold_pages", "source_files", "page_nos", "image_paths"):
            out[key] = json.loads(out[key] or "[]")
        out["model_verified"] = bool(out["model_verified"])
        return out

    def list_candidates(self, status: str = "pending", limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        if status not in ALLOWED_STATUSES and status != "all":
            raise ValueError("invalid status")
        sql = "SELECT * FROM candidates"
        args: List[Any] = []
        if status != "all":
            sql += " WHERE status=?"; args.append(status)
        sql += " ORDER BY created_at,id LIMIT ? OFFSET ?"; args.extend([max(1, min(limit, 200)), max(0, offset)])
        with self._connect() as db:
            return [self._decode(row) for row in db.execute(sql, args)]

    def get_candidate(self, cid: str) -> Optional[Dict[str, Any]]:
        with self._connect() as db:
            row=db.execute("SELECT * FROM candidates WHERE id=?",(cid,)).fetchone()
        return self._decode(row) if row else None

    def update_review(self, cid: str, status: str, query: Optional[str] = None,
                      gold_answer: Optional[str] = None, gold_branch: Optional[str] = None,
                      category: Optional[str] = None, reviewer_note: str = "") -> Dict[str, Any]:
        if status not in ALLOWED_STATUSES:
            raise ValueError("invalid status")
        if gold_branch is not None and gold_branch not in ALLOWED_BRANCHES:
            raise ValueError("invalid gold_branch")
        fields = ["status=?", "reviewer_note=?", "updated_at=?"]
        args: List[Any] = [status, reviewer_note, self._now()]
        for name, value in (("query", query), ("gold_answer", gold_answer), ("gold_branch", gold_branch), ("category", category)):
            if value is not None:
                if name in {"query", "gold_answer"} and not str(value).strip(): raise ValueError(f"{name} cannot be empty")
                fields.append(f"{name}=?"); args.append(str(value).strip())
        args.append(cid)
        with self._lock, self._connect() as db:
            cur = db.execute(f"UPDATE candidates SET {','.join(fields)} WHERE id=?", args)
            if cur.rowcount != 1: raise KeyError(cid)
            row = db.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
        return self._decode(row)

    def stats(self) -> Dict[str, int]:
        result = {status: 0 for status in ALLOWED_STATUSES}
        with self._connect() as db:
            for status, count in db.execute("SELECT status,COUNT(*) FROM candidates GROUP BY status"):
                result[str(status)] = int(count)
        result["total"] = sum(result.values())
        return result

    def export_rows(self, status: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []; offset=0
        while True:
            batch=self.list_candidates(status=status,limit=200,offset=offset)
            if not batch: break
            rows.extend(batch); offset+=len(batch)
            if len(batch)<200: break
        return rows
