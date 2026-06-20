#!/usr/bin/env python3
"""无需付费模型的研究任务 API 冒烟。"""
import argparse, json, time, urllib.request

def call(base, path, method="GET", payload=None):
    data=json.dumps(payload).encode() if payload is not None else None
    req=urllib.request.Request(base+path,data=data,method=method,headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as r: return json.loads(r.read() or b"{}")

parser=argparse.ArgumentParser();parser.add_argument("--base",default="http://127.0.0.1:8000");args=parser.parse_args()
ws=call(args.base,"/workspaces","POST",{"name":"Research smoke","use_demo":True})
job=call(args.base,"/research/jobs","POST",{"workspace_id":ws["workspace_id"],"objective":"对比这些资料中的关键指标，找出差异和潜在风险，并给出引用依据。"})
for _ in range(120):
    state=call(args.base,f"/research/jobs/{job['job_id']}"); print(state["status"],state["progress"])
    if state["status"] in ("completed","failed","cancelled"): break
    time.sleep(.25)
if state["status"] != "completed": raise SystemExit(f"research smoke failed: {state}")
report=call(args.base,f"/research/jobs/{job['job_id']}/report")
print("OK",report["title"],"citations=",len(report["citations"]))
