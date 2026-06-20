#!/usr/bin/env python3
"""企业 RAG API 并发压测：分阶段升压、混合流量、延迟分位与 JSON/Markdown 报告。"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import httpx


@dataclass
class Sample:
    operation: str
    status: int
    elapsed_ms: float
    ok: bool
    error: str = ""


def percentile(values: List[float], p: float) -> float:
    if not values: return 0.0
    ordered=sorted(values); rank=(len(ordered)-1)*p; lo=math.floor(rank); hi=math.ceil(rank)
    return ordered[lo] if lo==hi else ordered[lo]+(ordered[hi]-ordered[lo])*(rank-lo)


def parse_stages(raw: str) -> List[Tuple[int,float]]:
    stages=[]
    for part in raw.split(","):
        users,seconds=part.strip().split(":",1); stages.append((int(users),float(seconds)))
    if not stages or any(u<1 or s<=0 for u,s in stages): raise ValueError("stages must look like 10:20,50:30")
    return stages


async def request_once(client: httpx.AsyncClient,args,worker_id: int) -> Sample:
    mode=args.mode
    if mode=="mixed": mode=random.choices(["status","ask","research"],weights=[args.status_weight,args.ask_weight,args.research_weight if args.workspace_id else 0])[0]
    started=time.perf_counter(); status=0
    try:
        if mode=="status": response=await client.get("/capabilities")
        elif mode=="ask":
            response=await client.post("/ask",json={"query":random.choice(args.queries),"topk":args.topk,"session_id":f"load-{worker_id}-{random.randint(1,100000)}","workspace_id":args.workspace_id})
        else:
            if not args.workspace_id: return Sample("research",0,0,False,"--workspace-id is required for research mode")
            response=await client.post("/research/jobs",json={"workspace_id":args.workspace_id,"objective":random.choice(args.research_objectives),"session_id":f"load-{worker_id}"})
        status=response.status_code
        ok=200<=status<300
        return Sample(mode,status,(time.perf_counter()-started)*1000,ok,"" if ok else response.text[:160])
    except Exception as exc: return Sample(mode,status,(time.perf_counter()-started)*1000,False,str(exc)[:160])


async def run_stage(client,args,users: int,duration: float,stage_no: int) -> List[Sample]:
    deadline=time.perf_counter()+duration; results=[]; lock=asyncio.Lock()
    async def worker(worker_id: int):
        local=[]
        while time.perf_counter()<deadline:
            local.append(await request_once(client,args,stage_no*100000+worker_id))
            if args.think_time>0: await asyncio.sleep(args.think_time)
        async with lock: results.extend(local)
    started=time.perf_counter(); await asyncio.gather(*(worker(i) for i in range(users)))
    elapsed=time.perf_counter()-started
    ok=sum(x.ok for x in results)
    print(f"stage={stage_no} users={users} duration={elapsed:.1f}s requests={len(results)} ok={ok} rps={len(results)/max(elapsed,.001):.2f}")
    return results


def summarize(samples: List[Sample],elapsed: float) -> Dict:
    def block(rows):
        lat=[x.elapsed_ms for x in rows]
        return {"requests":len(rows),"success":sum(x.ok for x in rows),"failed":sum(not x.ok for x in rows),"success_rate":round(sum(x.ok for x in rows)/len(rows),4) if rows else 0,"rps":round(len(rows)/max(elapsed,.001),2),"latency_ms":{"avg":round(statistics.fmean(lat),2) if lat else 0,"p50":round(percentile(lat,.50),2),"p90":round(percentile(lat,.90),2),"p95":round(percentile(lat,.95),2),"p99":round(percentile(lat,.99),2),"max":round(max(lat),2) if lat else 0},"status_codes":dict(Counter(str(x.status or "error") for x in rows))}
    grouped=defaultdict(list)
    for sample in samples: grouped[sample.operation].append(sample)
    return {"elapsed_seconds":round(elapsed,2),"overall":block(samples),"operations":{name:block(rows) for name,rows in grouped.items()},"errors":dict(Counter(x.error for x in samples if x.error).most_common(10))}


def markdown(report: Dict) -> str:
    rows=["# RAG 并发压测报告","",f"- 时间：{report['created_at']}",f"- 地址：{report['base_url']}",f"- 模式：{report['mode']}",f"- 阶段：{report['stages']}","","| 操作 | 请求 | 成功率 | RPS | P50 | P95 | P99 | Max |","|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name,data in {"overall":report["summary"]["overall"],**report["summary"]["operations"]}.items():
        lat=data["latency_ms"]; rows.append(f"| {name} | {data['requests']} | {data['success_rate']*100:.2f}% | {data['rps']} | {lat['p50']} | {lat['p95']} | {lat['p99']} | {lat['max']} |")
    rows += ["","## HTTP 状态码","",f"```json\n{json.dumps(report['summary']['overall']['status_codes'],ensure_ascii=False,indent=2)}\n```","","## 主要错误","",f"```json\n{json.dumps(report['summary']['errors'],ensure_ascii=False,indent=2)}\n```"]
    return "\n".join(rows)


async def async_main(args) -> int:
    limits=httpx.Limits(max_connections=max(u for u,_ in args.parsed_stages)+20,max_keepalive_connections=max(u for u,_ in args.parsed_stages))
    timeout=httpx.Timeout(args.timeout)
    started=time.perf_counter(); samples=[]
    async with httpx.AsyncClient(base_url=args.base.rstrip("/"),limits=limits,timeout=timeout) as client:
        health=await client.get("/health"); health.raise_for_status()
        for no,(users,duration) in enumerate(args.parsed_stages,1): samples.extend(await run_stage(client,args,users,duration,no))
    elapsed=time.perf_counter()-started
    report={"created_at":datetime.now().isoformat(timespec="seconds"),"base_url":args.base,"mode":args.mode,"stages":args.stages,"config":{"topk":args.topk,"think_time":args.think_time,"timeout":args.timeout,"workspace_id":args.workspace_id},"summary":summarize(samples,elapsed)}
    out_dir=Path(args.output_dir); out_dir.mkdir(parents=True,exist_ok=True); stamp=datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path=out_dir/f"load-test-{stamp}.json"; md_path=out_dir/f"load-test-{stamp}.md"
    json_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"); md_path.write_text(markdown(report),encoding="utf-8")
    print(json.dumps(report["summary"],ensure_ascii=False,indent=2)); print(f"reports: {json_path} {md_path}")
    return 0 if report["summary"]["overall"]["success_rate"]>=args.min_success_rate else 1


def main() -> int:
    parser=argparse.ArgumentParser(description="RAG API 并发与容量压测")
    parser.add_argument("--base",default="http://127.0.0.1:8000"); parser.add_argument("--mode",choices=["status","ask","research","mixed"],default="mixed")
    parser.add_argument("--stages",default="10:10,50:20,100:30",help="并发数:秒，逗号分隔")
    parser.add_argument("--workspace-id",default=None); parser.add_argument("--topk",type=int,default=3); parser.add_argument("--timeout",type=float,default=30)
    parser.add_argument("--think-time",type=float,default=.2); parser.add_argument("--output-dir",default="reports/load")
    parser.add_argument("--min-success-rate",type=float,default=.95)
    parser.add_argument("--status-weight",type=float,default=6); parser.add_argument("--ask-weight",type=float,default=3); parser.add_argument("--research-weight",type=float,default=1)
    parser.add_argument("--queries",nargs="+",default=["采购申请单的采购单号是多少？","哪个产品线销售额最高？","总结当前资料的主要内容"])
    parser.add_argument("--research-objectives",nargs="+",default=["对比资料中的关键指标并识别风险","汇总资料的核心结论并给出引用"])
    args=parser.parse_args(); args.parsed_stages=parse_stages(args.stages)
    return asyncio.run(async_main(args))


if __name__=="__main__": raise SystemExit(main())
