#!/usr/bin/env python3
"""将人工审核通过的候选导出为不可变金标版本。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path

from src.config import SETTINGS
from src.gold_dataset import GoldReviewStore


def file_sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""): h.update(block)
    return h.hexdigest()


def git_sha() -> str:
    try: return subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
    except Exception: return "unknown"


def write_jsonl(path: Path,rows: list) -> None:
    path.write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in rows),encoding="utf-8")


def clean(row: dict) -> dict:
    return {key:row[key] for key in (
        "id","query","gold_answer","gold_pages","gold_branch","category","source_files","page_nos",
        "image_paths","model_verified","model_reason","reviewer_note","updated_at"
    )}


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--db",default="data/gold_review/review.db")
    ap.add_argument("--pages",default="data/user_pages.json"); ap.add_argument("--root",default="data/eval_sets")
    ap.add_argument("--tag",default="gold"); ap.add_argument("--generator-model",default=SETTINGS.qwen_vlm_model)
    ap.add_argument("--verifier-model",default=SETTINGS.qwen_vlm_verifier_model); args=ap.parse_args()
    store=GoldReviewStore(args.db); accepted=[clean(x) for x in store.export_rows("accepted")]
    if not accepted: raise SystemExit("没有人工审核通过的候选，不能生成金标版本")
    rejected=[clean(x) for x in store.export_rows("rejected")]
    pending=[clean(x) for x in store.export_rows("pending")]
    version=datetime.now().strftime("v%Y%m%d-%H%M%S")+(f"-{args.tag}" if args.tag else "")
    out=Path(args.root)/version; out.mkdir(parents=True,exist_ok=False)
    write_jsonl(out/"gold.jsonl",accepted); write_jsonl(out/"rejected.jsonl",rejected); write_jsonl(out/"pending.jsonl",pending)
    pages=Path(args.pages)
    meta={"version":version,"created_at":datetime.now().isoformat(timespec="seconds"),"sample_count":len(accepted),
          "categories":dict(Counter(x["category"] for x in accepted)),"branches":dict(Counter(x["gold_branch"] for x in accepted)),
          "generator_model":args.generator_model,"verifier_model":args.verifier_model,"review_policy":"human_accept_only",
          "index_file":str(pages),"index_sha256":file_sha256(pages) if pages.exists() else "missing","git_commit":git_sha(),
          "review_stats":store.stats()}
    (out/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"version":version,"output":str(out),"gold":len(accepted),"rejected":len(rejected),"pending":len(pending)},ensure_ascii=False))
    return 0


if __name__ == "__main__": raise SystemExit(main())
