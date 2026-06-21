#!/usr/bin/env python3
"""对正式 gold.jsonl 调用在线 /ask，输出检索、生成、路由和工程指标。"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List

from src.eval_metrics import accuracy, mrr_at_k, recall_at_k


def read_jsonl(path: Path) -> List[Dict[str,Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def ask(base: str,query: str,topk: int,timeout: float) -> Dict[str,Any]:
    req=urllib.request.Request(base.rstrip("/")+"/ask",data=json.dumps({"query":query,"topk":topk},ensure_ascii=False).encode(),headers={"content-type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=timeout) as resp: return json.loads(resp.read().decode())


def avg(values: List[float]) -> float: return mean(values) if values else 0.0


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--gold",required=True); ap.add_argument("--base",default="http://127.0.0.1:8000")
    ap.add_argument("--topk",type=int,default=10); ap.add_argument("--timeout",type=float,default=180)
    ap.add_argument("--output-dir",default="reports/eval"); args=ap.parse_args()
    rows=read_jsonl(Path(args.gold)); details=[]; stage_costs=defaultdict(list)
    metrics=defaultdict(list); errors=0
    for index,row in enumerate(rows,1):
        started=time.perf_counter()
        try:
            data=ask(args.base,row["query"],args.topk,args.timeout); elapsed=int((time.perf_counter()-started)*1000)
            ranked=[str(x.get("page_id")) for x in data.get("hits",[])]
            gold=row["gold_pages"]; answer_ok=accuracy(str(data.get("answer") or ""),str(row["gold_answer"]))
            vals={"mrr_at_10":mrr_at_k(ranked,gold,10),"recall_at_1":recall_at_k(ranked,gold,1),
                  "recall_at_3":recall_at_k(ranked,gold,3),"recall_at_10":recall_at_k(ranked,gold,10),
                  "answer_accuracy":float(answer_ok),"router_accuracy":float(data.get("branch")==row["gold_branch"]),
                  "verifier_pass_rate":float(bool(data.get("verified"))),
                  "fallback_rate":float(bool((data.get("trace") or {}).get("fallback_triggered")))}
            for key,value in vals.items(): metrics[key].append(value)
            for stage in (data.get("trace") or {}).get("stages",[]): stage_costs[str(stage.get("stage"))].append(float(stage.get("elapsed_ms") or 0))
            reasons=[]
            if not vals["recall_at_10"]: reasons.append("gold_page_not_in_top10")
            if not answer_ok: reasons.append("answer_mismatch")
            if not vals["router_accuracy"]: reasons.append("router_mismatch")
            details.append({"id":row["id"],"query":row["query"],"gold_answer":row["gold_answer"],"predicted_answer":data.get("answer"),
                            "gold_pages":gold,"ranked_pages":ranked,"gold_rank":next((i+1 for i,x in enumerate(ranked) if x in set(gold)),None),
                            "gold_branch":row["gold_branch"],"predicted_branch":data.get("branch"),"verified":data.get("verified"),
                            "cost_ms":data.get("cost_ms",elapsed),"metrics":vals,"failure_reasons":reasons})
            print(f"[{index}/{len(rows)}] {row['id']} recall10={vals['recall_at_10']:.0f} answer={answer_ok} branch={data.get('branch')}",flush=True)
        except Exception as exc:
            errors+=1; details.append({"id":row.get("id"),"query":row.get("query"),"error":f"{type(exc).__name__}: {exc}"})
    summary={key:round(avg(values),6) for key,values in metrics.items()}
    summary.update({"sample_count":len(rows),"completed":len(rows)-errors,"errors":errors,
                    "avg_stage_latency_ms":{key:round(avg(values),2) for key,values in stage_costs.items()}})
    report={"created_at":datetime.now().isoformat(timespec="seconds"),"gold":str(Path(args.gold).resolve()),"base":args.base,"topk":args.topk,"summary":summary,"details":details}
    out_dir=Path(args.output_dir); out_dir.mkdir(parents=True,exist_ok=True); stamp=datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path=out_dir/f"gold-eval-{stamp}.json"; md_path=out_dir/f"gold-eval-{stamp}.md"
    json_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# 金标评测报告","",f"- 样本：{len(rows)}",f"- 完成：{len(rows)-errors}",f"- 错误：{errors}",""]
    for key,value in summary.items():
        if isinstance(value,(float,int)) and key not in {"sample_count","completed","errors"}: lines.append(f"- {key}: {value:.4f}")
    lines += ["","## 失败样本",""]
    for item in details:
        if item.get("error") or item.get("failure_reasons"): lines.append(f"- `{item.get('id')}` {item.get('failure_reasons') or item.get('error')}")
    md_path.write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({"summary":summary,"json":str(json_path),"markdown":str(md_path)},ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__": raise SystemExit(main())
