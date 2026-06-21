#!/usr/bin/env python3
"""把外部模型生成的页码型 JSON 映射到页面索引并导入金标审核库。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

from src.gold_dataset import GoldReviewStore
from src.models import Page


def load_document_pages(index_path: Path, document_keyword: str) -> Dict[int, Page]:
    raw=json.loads(index_path.read_text(encoding="utf-8"))
    matched=[]
    for item in raw:
        page=Page(**item)
        source=Path(page.source_file).name if page.source_file else page.doc_id
        if document_keyword.lower() in source.lower(): matched.append(page)
    if not matched: raise ValueError(f"页面索引中未找到文档: {document_keyword}")
    doc_ids={page.doc_id for page in matched}
    if len(doc_ids)!=1: raise ValueError(f"文档关键词匹配到多个 doc_id: {sorted(doc_ids)}")
    by_no={int(page.page_no):page for page in matched if page.page_no is not None}
    if not by_no: raise ValueError("目标文档没有可用 page_no")
    return by_no


def main() -> int:
    ap=argparse.ArgumentParser(description="Import external gold QA JSON into local review database")
    ap.add_argument("--input",required=True,help="JSON数组，字段含 query/gold_answer/page_nos/gold_branch/category")
    ap.add_argument("--document",required=True,help="页面索引中的源文件名关键词")
    ap.add_argument("--pages",default="data/user_pages.json"); ap.add_argument("--db",default="data/gold_review/review.db")
    args=ap.parse_args()
    rows=json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(rows,list): raise SystemExit("输入必须是 JSON 数组")
    by_no=load_document_pages(Path(args.pages),args.document); store=GoldReviewStore(args.db)
    imported=skipped=0; errors: List[dict]=[]
    required={"query","gold_answer","page_nos","gold_branch","category"}
    for index,row in enumerate(rows,1):
        try:
            missing=required-set(row)
            if missing: raise ValueError(f"缺少字段: {sorted(missing)}")
            page_nos=row["page_nos"]
            if not isinstance(page_nos,list) or not page_nos: raise ValueError("page_nos 必须是非空数组")
            pages=[]
            for value in page_nos:
                number=int(value)
                if number not in by_no: raise ValueError(f"页码不在目标文档索引中: {number}")
                pages.append(by_no[number])
            payload={"query":row["query"],"gold_answer":row["gold_answer"],"gold_branch":row["gold_branch"],
                     "category":row["category"],"gold_pages":[p.page_id for p in pages],
                     "source_files":[str(p.source_file or p.doc_id) for p in pages],"page_nos":[p.page_no for p in pages],
                     "image_paths":[str(p.image_path or "") for p in pages],"model_verified":False,
                     "model_reason":"外部模型生成，尚未经过本项目独立校验；需人工审核后才能进入正式金标集。"}
            before=store.stats()["total"]; store.upsert_candidate(payload); after=store.stats()["total"]
            imported+=int(after>before); skipped+=int(after==before)
        except Exception as exc:
            errors.append({"row":index,"error":str(exc)})
    result={"input_rows":len(rows),"imported":imported,"already_exists":skipped,"errors":errors,"review":store.stats()}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if errors else 0


if __name__ == "__main__": raise SystemExit(main())
