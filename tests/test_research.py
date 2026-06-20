import os
import time
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ.update({"OPENAI_API_KEY":"","OAPI_API_KEY":"","RAG_ENABLE_LLM_ROUTER":"false","RAG_ENABLE_LLM_VERIFIER":"false","RAG_ENABLE_FUNCTION_CALLING_ROUTER":"false","RAG_ENABLE_REAL_EMBEDDING":"false","RAG_ENABLE_MULTIMODAL_EMBEDDING":"false","RAG_ENABLE_COLPALI_RERANK":"false","RAG_VECTOR_BACKEND":"inmemory","RAG_SESSION_BACKEND":"memory","RAG_ENABLE_RATE_LIMIT":"false"})

import pytest
from fastapi.testclient import TestClient

from src.infra.research_repository import SQLiteResearchRepository
from src.research import ReportGenerator, ResearchPlanner, ToolExecutionError, ToolRegistry
from src.research import ResearchExecutor
from src.research_models import validate_job_transition
from src.bootstrap import build_engine_from_pages
from src.models import Page


def test_workspace_crud_and_isolation(tmp_path):
    repo=SQLiteResearchRepository(str(tmp_path/"research.db"))
    a=repo.create_workspace("A","",False); b=repo.create_workspace("B","",False)
    now="2026-01-01T00:00:00+00:00"
    doc={"document_id":"d1","workspace_id":a["workspace_id"],"file_name":"a.txt","source_path":"/tmp/a.txt","content_type":"text/plain","status":"ready","page_count":1,"error_message":"","created_at":now,"updated_at":now}
    repo.add_document(doc,[{"page_id":"p1","doc_id":"d1","doc_type":"manual","language":"zh","content":"secret"}])
    assert len(repo.list_pages(a["workspace_id"])) == 1
    assert repo.list_pages(b["workspace_id"]) == []
    assert repo.delete_workspace(a["workspace_id"])
    assert repo.get_workspace(a["workspace_id"]) is None


def test_cancel_pending_job(tmp_path):
    repo=SQLiteResearchRepository(str(tmp_path/"cancel.db")); ws=repo.create_workspace("A","",True)
    payload={"job_id":"j","workspace_id":ws["workspace_id"],"session_id":"s","objective":"x","status":"pending","progress":0,"current_step":"","plan":[],"findings":[],"report_id":None,"error_message":"","created_at":"2026-01-01T00:00:00+00:00","started_at":None,"finished_at":None,"idempotency_key":None}
    repo.save_job(payload); assert repo.request_cancel("j"); assert repo.get_job("j")["status"] == "cancelled"; assert not repo.request_cancel("j")
    stale={**payload,"status":"completed","progress":100}
    assert not repo.save_job(stale)
    assert repo.get_job("j")["status"] == "cancelled"


def test_idempotency_is_atomic(tmp_path):
    repo=SQLiteResearchRepository(str(tmp_path/"idem.db")); ws=repo.create_workspace("A","",True)
    def create(i):
        payload={"job_id":f"j{i}","workspace_id":ws["workspace_id"],"session_id":"s","objective":"x","status":"pending","progress":0,"current_step":"","plan":[],"findings":[],"report_id":None,"error_message":"","created_at":f"2026-01-01T00:00:00.00000{i}+00:00","started_at":None,"finished_at":None,"idempotency_key":"same"}
        return repo.create_job(payload)
    with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(create,range(8)))
    assert len({item[0]["job_id"] for item in results}) == 1
    assert sum(1 for _,created in results if created) == 1


def test_workspace_engine_build_is_cached_under_concurrency(tmp_path,monkeypatch):
    repo=SQLiteResearchRepository(str(tmp_path/"cache.db")); ws=repo.create_workspace("A","",True)
    calls=[]; sentinel=object()
    def fake_build(pages): calls.append(len(pages)); time.sleep(.03); return sentinel
    monkeypatch.setattr("src.research.build_engine_from_pages",fake_build)
    executor=ResearchExecutor(repo)
    with ThreadPoolExecutor(max_workers=8) as pool: engines=list(pool.map(lambda _:executor.build_workspace_engine(ws["workspace_id"]),range(8)))
    assert engines == [sentinel]*8 and len(calls) == 1


@pytest.mark.parametrize("objective,tool",[("查询负责人","fact_qa"),("对比三份报告差异和趋势","multi_page_qa"),("收入最高金额是多少","chart_qa")])
def test_rule_planner(objective,tool):
    steps=ResearchPlanner().plan(objective)
    assert 2 <= len(steps) <= 6
    assert tool in [x.tool_name for x in steps]


class FakeEngine:
    def ask(self,*args,**kwargs): raise RuntimeError("boom")


class SlowEngine:
    def ask(self,*args,**kwargs): time.sleep(.2)


def test_tool_registry_validation_and_error():
    registry=ToolRegistry(FakeEngine(),timeout_seconds=1)
    with pytest.raises(ToolExecutionError,match="unknown tool"): registry.execute("shell",{"query":"x"})
    with pytest.raises(ToolExecutionError,match="query"): registry.execute("fact_qa",{"query":""})
    with pytest.raises(ToolExecutionError) as caught: registry.execute("fact_qa",{"query":"x"})
    assert caught.value.code == "execution_failed"


def test_tool_timeout_returns_without_waiting_for_work():
    started=time.perf_counter()
    with pytest.raises(ToolExecutionError) as caught: ToolRegistry(SlowEngine(),timeout_seconds=.01).execute("fact_qa",{"query":"x"})
    assert caught.value.code == "timeout"
    assert time.perf_counter()-started < .1


def test_registry_executes_the_selected_branch():
    pages=[Page(page_id="p",doc_id="d",doc_type="manual",language="zh",content="负责人是李雷",people=["李雷"])]
    engine=build_engine_from_pages(pages)
    result=ToolRegistry(engine).execute("multi_page_qa",{"query":"归纳负责人","topk":1})["result"]
    assert result.trace.route_branch == "multi_page_qa"


def test_job_state_machine():
    validate_job_transition("pending","planning")
    with pytest.raises(ValueError): validate_job_transition("completed","running")


def test_report_escapes_html_and_does_not_invent_page_number():
    job={"job_id":"j","objective":"<script>alert(1)</script>","findings":[{"title":"发现","answer":"安全结论","verified":True,"evidence":[{"doc_id":"d","file_name":"a.pdf","page_id":"p-x","page_no":None,"score":1.0,"excerpt":"<img src=x onerror=1>","source_type":"pdf"}]}]}
    report=ReportGenerator().generate(job,[])
    assert "[a.pdf, p-x]" in report.markdown_content
    assert "<script>" not in report.html_content and "&lt;script&gt;" in report.html_content
    assert "onerror=1>" not in report.html_content


def test_api_happy_path_and_ask_regression(tmp_path, monkeypatch):
    import src.api as api
    repo=SQLiteResearchRepository(str(tmp_path/"api.db")); api.research_repository=repo; api.research_executor.repo=repo
    client=TestClient(api.app)
    old=client.post("/ask",json={"query":"产品线最高销售额是多少"})
    assert old.status_code == 200 and "answer" in old.json()
    ws=client.post("/workspaces",json={"name":"演示空间","use_demo":True})
    assert ws.status_code == 201; wid=ws.json()["workspace_id"]
    bad=client.post(f"/workspaces/{wid}/documents",files={"file":("../escape.txt",b"x","text/plain")})
    assert bad.status_code == 400
    unsupported=client.post(f"/workspaces/{wid}/documents",files={"file":("bad.exe",b"x","application/octet-stream")})
    assert unsupported.status_code == 400
    monkeypatch.setenv("RAG_UPLOAD_MAX_BYTES","2")
    too_large=client.post(f"/workspaces/{wid}/documents",files={"file":("large.txt",b"abc","text/plain")})
    assert too_large.status_code == 413
    monkeypatch.delenv("RAG_UPLOAD_MAX_BYTES")
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as archive: archive.writestr("word/document.xml","x"*100)
    monkeypatch.setenv("RAG_UPLOAD_MAX_UNCOMPRESSED_BYTES","10")
    expanded=client.post(f"/workspaces/{wid}/documents",files={"file":("bomb.docx",buf.getvalue(),"application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert expanded.status_code == 413
    monkeypatch.delenv("RAG_UPLOAD_MAX_UNCOMPRESSED_BYTES")
    scoped=client.post("/ask",json={"query":"负责人是谁","workspace_id":wid})
    assert scoped.status_code == 200
    submitted=client.post("/research/jobs",json={"workspace_id":wid,"objective":"对比这些资料中的关键指标，找出差异和潜在风险，并给出引用依据。","idempotency_key":"once"})
    assert submitted.status_code == 202; jid=submitted.json()["job_id"]
    again=client.post("/research/jobs",json={"workspace_id":wid,"objective":"ignored","idempotency_key":"once"})
    assert again.json()["job_id"] == jid
    for _ in range(100):
        state=client.get(f"/research/jobs/{jid}").json()
        if state["status"] in {"completed","failed"}: break
        time.sleep(.03)
    assert state["status"] == "completed", state
    assert client.get(f"/research/jobs/{jid}/report.md").status_code == 200
    html=client.get(f"/research/jobs/{jid}/report.html")
    assert html.status_code == 200 and "default-src 'none'" in html.headers["content-security-policy"]


def test_workspace_delete_rejected_with_active_job(tmp_path):
    import src.api as api
    old_repo,old_executor=api.research_repository,api.research_executor.repo
    repo=SQLiteResearchRepository(str(tmp_path/"busy.db")); api.research_repository=repo; api.research_executor.repo=repo
    try:
        client=TestClient(api.app); ws=client.post("/workspaces",json={"name":"busy","use_demo":True}).json()
        payload={"job_id":"busy-job","workspace_id":ws["workspace_id"],"session_id":"s","objective":"x","status":"running","progress":10,"current_step":"","plan":[],"findings":[],"report_id":None,"error_message":"","created_at":"2026-01-01T00:00:00+00:00","started_at":None,"finished_at":None,"idempotency_key":None}
        repo.save_job(payload)
        response=client.delete(f"/workspaces/{ws['workspace_id']}")
        assert response.status_code == 409 and response.json()["detail"]["code"] == "workspace_busy"
    finally:
        api.research_repository,api.research_executor.repo=old_repo,old_executor


def test_anonymous_conversation_history_is_isolated(tmp_path):
    repo=SQLiteResearchRepository(str(tmp_path/"conversation.db"))
    conversation=repo.create_conversation("client-aaaaaaaa",None,"新对话")
    cid=conversation["conversation_id"]
    repo.add_message(cid,"user","这是第一条问题",{"topk":3})
    repo.add_message(cid,"assistant","这是回答",{"verified":True})
    assert repo.list_conversations("client-aaaaaaaa")[0]["title"] == "这是第一条问题"
    assert [x["role"] for x in repo.list_messages(cid,"client-aaaaaaaa")] == ["user","assistant"]
    assert repo.list_messages(cid,"client-bbbbbbbb") is None
    assert not repo.delete_conversation(cid,"client-bbbbbbbb")
    assert repo.delete_conversation(cid,"client-aaaaaaaa")


def test_api_conversation_and_sse_events(tmp_path):
    import src.api as api
    old_repo,old_executor=api.research_repository,api.research_executor.repo
    repo=SQLiteResearchRepository(str(tmp_path/"events.db")); api.research_repository=repo; api.research_executor.repo=repo
    try:
        client=TestClient(api.app)
        created=client.post("/conversations",json={"client_id":"client-aaaaaaaa","title":"新对话"})
        assert created.status_code == 201; cid=created.json()["conversation_id"]
        asked=client.post("/ask",json={"query":"产品线最高销售额是多少","client_id":"client-aaaaaaaa","conversation_id":cid,"session_id":cid})
        assert asked.status_code == 200 and asked.json()["conversation_id"] == cid
        messages=client.get(f"/conversations/{cid}/messages?client_id=client-aaaaaaaa")
        assert [x["role"] for x in messages.json()] == ["user","assistant"]
        assert client.get(f"/conversations/{cid}/messages?client_id=client-bbbbbbbb").status_code == 404

        ws=client.post("/workspaces",json={"name":"SSE","use_demo":True}).json()
        submitted=client.post("/research/jobs",json={"workspace_id":ws["workspace_id"],"objective":"归纳资料中的核心结论并提供证据"})
        jid=submitted.json()["job_id"]
        for _ in range(200):
            job=client.get(f"/research/jobs/{jid}").json()
            if job["status"] in {"completed","failed","cancelled"}: break
            time.sleep(.02)
        assert job["status"] == "completed", job
        stream=client.get(f"/research/jobs/{jid}/events")
        assert stream.status_code == 200
        assert "event: job_created" in stream.text
        assert "event: plan_created" in stream.text
        assert "event: job_completed" in stream.text
        assert stream.headers["content-type"].startswith("text/event-stream")
    finally:
        api.research_repository,api.research_executor.repo=old_repo,old_executor
