#!/usr/bin/env python3
"""只绑定本机的金标候选审核服务。"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.gold_dataset import GoldReviewStore


class ReviewRequest(BaseModel):
    status: str
    query: Optional[str] = None
    gold_answer: Optional[str] = None
    gold_branch: Optional[str] = None
    category: Optional[str] = None
    reviewer_note: str = ""


def create_app(db_path: str) -> FastAPI:
    store=GoldReviewStore(db_path); app=FastAPI(title="Gold Dataset Reviewer",docs_url=None,redoc_url=None)

    @app.get("/")
    def index(): return FileResponse(Path(__file__).resolve().parent.parent/"web"/"gold_review.html")

    @app.get("/api/stats")
    def stats(): return store.stats()

    @app.get("/api/candidates")
    def candidates(status: str="pending",limit: int=Query(20,ge=1,le=100),offset: int=Query(0,ge=0)):
        try: return {"items":store.list_candidates(status,limit,offset),"stats":store.stats()}
        except ValueError as exc: raise HTTPException(400,str(exc))

    @app.post("/api/candidates/{candidate_id}/review")
    def review(candidate_id: str,body: ReviewRequest):
        payload=body.model_dump() if hasattr(body,"model_dump") else body.dict()
        try: return store.update_review(candidate_id,**payload)
        except KeyError: raise HTTPException(404,"candidate not found")
        except ValueError as exc: raise HTTPException(400,str(exc))

    @app.get("/api/candidates/{candidate_id}/image/{index}")
    def image(candidate_id: str,index: int):
        row=store.get_candidate(candidate_id)
        if not row: raise HTTPException(404,"candidate not found")
        paths=row["image_paths"]
        if index<0 or index>=len(paths): raise HTTPException(404,"image not found")
        path=Path(paths[index]).resolve()
        if not path.is_file(): raise HTTPException(404,"image not found")
        return FileResponse(path)
    return app


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--db",default="data/gold_review/review.db")
    ap.add_argument("--host",default="127.0.0.1"); ap.add_argument("--port",type=int,default=8765); args=ap.parse_args()
    if args.host not in {"127.0.0.1","localhost","::1"}: raise SystemExit("审核服务只允许绑定本机回环地址")
    uvicorn.run(create_app(args.db),host=args.host,port=args.port,log_level="info")
    return 0


if __name__ == "__main__": raise SystemExit(main())
