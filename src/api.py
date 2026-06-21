"""
api.py — HTTP 入口（FastAPI）：把浏览器 / curl 请求转给编排引擎

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
- 模块加载时 `build_engine` / `build_agent_loop`：准备好「一辆车」（引擎 + 可选外层 loop）。
- `POST /ask`：一次用户提问的**边界**；内部要么 `agent_loop.run`（多轮扩 top-k），要么 `engine.ask`（单轮）。
- 返回 `AskResponse`：把 `branch`、`verified`、`hits`、`loop_steps` 暴露给前端，对应简历里的「可解释 Agent」。

================================================================================
【类比 Android】
================================================================================
- `FastAPI()` ≈ 定义一套 **Retrofit + 内嵌小型 WebServer**（实际是 ASGI）：`@app.get` / `@app.post` 像接口声明。
- `AskRequest` / `AskResponse`（Pydantic）≈ **Gson/Moshi data class** 或 Kotlin `@Serializable`：自动校验 JSON 字段类型与范围。
- 模块级 `engine = build_engine(...)`：类似 `Application.onCreate` 里初始化全局单例 Repository（注意：改 data_path 需重启进程才重新 build）。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `@app.post("/ask", response_model=AskResponse)`：装饰器 = 给函数「注册路由 + 响应模型」；`response_model` 控制序列化形状。
- `List[Dict[str, Any]]`：`typing` 泛型；`Any` 像「未擦除的 Object」，JSON 里嵌套结构常用。
- `if req.topk else engine.ask(req.query)`：Python 里 `0` 也会走 falsy 分支；这里 topk 为正整数时语义正常。
- 列表推导 `[{...} for s in loop_run.steps]`：类似 `loopRun.getSteps().stream().map(...).toList()`。
- `time.perf_counter()`：单调时钟测耗时，比 `System.nanoTime` 心智负担低，适合做 `cost_ms`。

轻量 API 服务入口（面试够用版）。
"""

from __future__ import annotations

from datetime import datetime
import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import FileResponse, Response, StreamingResponse
import sentry_sdk

from .agent_loop import PlanExecuteAgentLoop
from .bootstrap import build_engine
from .config import SETTINGS
from .middleware_ops import RateLimitMiddleware, RequestTimingMiddleware
from .router import router_circuit_open
from .services import VLMClient, vlm_circuit_open
from .eval_suite import DEFAULT_EVAL_SAMPLES, run_eval_report
from .infra.eval_report_store import load_latest_eval_report, save_eval_report
from .infra.document_ingest import ingest_document
from .infra.research_repository import SQLiteResearchRepository
from .infra.redis_memory import RedisSessionMemory
from .infra.vector_store import MilvusVectorStore
from .research import InProcessJobDispatcher, RESEARCH_JOBS, ResearchExecutor
from .research_models import ResearchJob, to_dict, utc_now
import os
import re
import shutil
import uuid
import zipfile

# 指标：总请求、耗时、回退、路由、校验、缓存、阶段耗时
REQ_COUNT = Counter("rag_requests_total", "Total requests", ["branch", "verified"])
REQ_LATENCY = Histogram("rag_request_latency_seconds", "Request latency seconds")
FALLBACK_COUNT = Counter("rag_fallback_total", "Fallback retries triggered")
ROUTER_COUNT = Counter("rag_router_total", "Router decision totals", ["route_branch", "final_branch"])
VERIFY_COUNT = Counter("rag_verify_total", "Verifier pass/fail totals", ["verified"])
CACHE_HIT_COUNT = Counter("rag_cache_hit_total", "Session cache hit totals")
RETRY_REASON_COUNT = Counter("rag_retry_reason_total", "Retry reason totals", ["reason"])
STAGE_LATENCY = Histogram("rag_stage_latency_seconds", "Per stage latency", ["stage"])
EVAL_RUN_COUNT = Counter("rag_eval_run_total", "Offline eval run totals")
DOCUMENT_INGEST = Counter("rag_document_ingest_total", "Document ingest", ["status", "type"])
DOCUMENT_INGEST_DURATION = Histogram("rag_document_ingest_duration_seconds", "Document ingest duration", ["type"])
app = FastAPI(title="Visual RAG Agent API", version="0.1.0")
app.add_middleware(RequestTimingMiddleware)
app.add_middleware(RateLimitMiddleware)
if SETTINGS.sentry_dsn:
    sentry_sdk.init(
        dsn=SETTINGS.sentry_dsn,
        traces_sample_rate=0.05,
        environment="prod" if not SETTINGS.enable_plan_execute_loop else "staging",
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in SETTINGS.cors_origins.split(",") if x.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
_user_pages = Path("data/user_pages.json")
_data_path = str(_user_pages) if _user_pages.exists() else "data/demo_pages.json"
engine = build_engine(_data_path)
agent_loop = PlanExecuteAgentLoop(engine=engine, max_loops=2)
research_repository = SQLiteResearchRepository(os.getenv("RAG_RESEARCH_DB", "data/research/research.db"))
research_executor = ResearchExecutor(research_repository)
research_dispatcher = InProcessJobDispatcher(research_executor)


class AskRequest(BaseModel):
    """问答请求模型。"""

    query: str = Field(..., min_length=1, description="用户问题")
    session_id: str = Field(default="default", min_length=1, max_length=128, description="会话 ID")
    topk: Optional[int] = Field(default=None, ge=1, le=20, description="可选 top-k")
    workspace_id: Optional[str] = Field(default=None, description="可选研究空间；传入后严格限制资料范围")
    client_id: Optional[str] = Field(default=None, min_length=8, max_length=128, description="匿名浏览器客户端 ID")
    conversation_id: Optional[str] = Field(default=None, min_length=8, max_length=128, description="持久对话 ID")


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    use_demo: bool = Field(default=True, description="是否把内置 demo pages 纳入本空间")


class ResearchJobRequest(BaseModel):
    workspace_id: str
    objective: str = Field(..., min_length=3, max_length=4000)
    session_id: str = Field(default="research", max_length=128)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class AskResponse(BaseModel):
    """问答响应模型（保留关键运行轨迹，方便面试演示可解释性）。"""

    answer: str
    branch: str
    verified: bool
    rewritten_query: str
    hits: List[Dict[str, Any]]
    retry_hits: Optional[List[Dict[str, Any]]] = None
    loop_steps: Optional[List[Dict[str, Any]]] = None
    trace: Optional[Dict[str, Any]] = None
    cost_ms: int
    source_files: List[str] = Field(default_factory=list, description="本次回答依据的源文件完整文件名（去重）")
    citations: List[Dict[str, Any]] = Field(default_factory=list, description="本次回答依据的页级证据片段")
    conversation_id: Optional[str] = None


class ConversationCreateRequest(BaseModel):
    client_id: str = Field(...,min_length=8,max_length=128)
    workspace_id: Optional[str] = None
    title: str = Field(default="新对话",max_length=80)


class EvalRunRequest(BaseModel):
    persist: bool = Field(default=True, description="是否保存评测报告到本地文件")
    tag: str = Field(default="", max_length=64, description="评测标签（用于文件名后缀）")


class EvalRunResponse(BaseModel):
    created_at: str
    persisted: bool
    report_path: Optional[str] = None
    summary: Dict[str, Any]
    per_category: List[Dict[str, Any]]
    engineering: Dict[str, Any]


@app.get("/health")
def health() -> Dict[str, str]:
    """健康检查接口。"""
    return {"status": "ok"}


@app.get("/")
def root_chat() -> FileResponse:
    """默认返回聊天页面。"""
    return FileResponse("web/chat.html")


@app.get("/chat")
def chat_page() -> FileResponse:
    """聊天页面（与 API 同域，避免 CORS 问题）。"""
    return FileResponse("web/chat.html")


@app.get("/capabilities")
def capabilities() -> Dict[str, Any]:
    """企业级能力接入状态。"""
    return {
        "multimodal_embedding": bool(SETTINGS.enable_multimodal_embedding and SETTINGS.multimodal_embedding_api),
        "milvus": isinstance(engine.retriever.vector_store, MilvusVectorStore),
        "colpali_rerank": bool(SETTINGS.enable_colpali_rerank and SETTINGS.colpali_rerank_api),
        "function_calling_router": bool(SETTINGS.enable_function_calling_router and SETTINGS.openai_api_key),
        "langgraph": SETTINGS.enable_langgraph,
        "vlm": VLMClient().enabled,
        "chart_parsing": bool(SETTINGS.chart_parsing_api),
        "bm25_fallback": SETTINGS.enable_bm25_fallback,
        "rate_limit": SETTINGS.enable_rate_limit,
        "router_circuit_breaker": SETTINGS.enable_router_circuit_breaker,
        "router_circuit_open": router_circuit_open(),
        "vlm_circuit_breaker": SETTINGS.enable_vlm_circuit_breaker,
        "vlm_circuit_open": vlm_circuit_open(),
        "local_colpali_model": Path(SETTINGS.colpali_model_dir).exists(),
        "local_colpali_model_dir": SETTINGS.colpali_model_dir,
        "redis_memory": isinstance(engine.memory, RedisSessionMemory),
        "session_cache": SETTINGS.enable_session_cache,
        "workspace": True,
        "research_jobs": True,
        "research_sse_events": True,
        "anonymous_conversations": True,
        "qwen_vision_parser": bool(SETTINGS.enable_qwen_vision_parser and SETTINGS.effective_openai_api_key),
        "qwen_vision_model": SETTINGS.vision_parser_model if SETTINGS.enable_qwen_vision_parser else "",
        "qwen_online_vlm_model": SETTINGS.qwen_vlm_model if SETTINGS.enable_qwen_vision_parser else "",
        "report_generation": True,
        "job_backend": "in_process_thread_pool",
        "agentic_rag": True,
        "ask_sse": True,
        "deep_research_plan_execute": True,
        "agentic_query_expansion": SETTINGS.enable_query_expansion,
        "agentic_retry_refine": SETTINGS.enable_agentic_retry_refine,
        "benchmark": {
            "recall_at_10": SETTINGS.benchmark_recall_at_10,
            "accuracy": SETTINGS.benchmark_accuracy,
            "router_accuracy": SETTINGS.benchmark_router_accuracy,
        },
    }


def _not_found(kind: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code":f"{kind}_not_found","message":f"{kind} not found"})


def _validate_document_container(path: Path, suffix: str) -> None:
    max_pages=int(os.getenv("RAG_UPLOAD_MAX_PAGES","500"))
    max_uncompressed=int(os.getenv("RAG_UPLOAD_MAX_UNCOMPRESSED_BYTES",str(200*1024*1024)))
    if suffix in {".docx",".xlsx",".pptx"}:
        try:
            with zipfile.ZipFile(path) as archive:
                infos=archive.infolist()
                if len(infos)>10000 or sum(x.file_size for x in infos)>max_uncompressed:
                    raise HTTPException(413,detail={"code":"document_too_large","message":"office document expands beyond safe limits"})
        except zipfile.BadZipFile as exc:
            raise HTTPException(400,detail={"code":"invalid_file","message":"invalid office container"}) from exc
    if suffix==".pdf":
        try:
            import fitz
            with fitz.open(str(path)) as pdf:
                if pdf.page_count>max_pages:
                    raise HTTPException(413,detail={"code":"too_many_pages","message":f"maximum {max_pages} pages"})
        except HTTPException: raise
        except Exception as exc:
            raise HTTPException(400,detail={"code":"invalid_file","message":"invalid PDF"}) from exc


@app.post("/workspaces", status_code=201)
def create_workspace(req: WorkspaceCreateRequest) -> Dict[str, Any]:
    return research_repository.create_workspace(req.name.strip(), req.description.strip(), req.use_demo)


@app.get("/workspaces")
def list_workspaces() -> List[Dict[str, Any]]: return research_repository.list_workspaces()


@app.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: str) -> Dict[str, Any]:
    ws=research_repository.get_workspace(workspace_id)
    if not ws: raise _not_found("workspace")
    ws["documents"]=research_repository.list_documents(workspace_id); return ws


@app.post("/conversations",status_code=201)
def create_conversation(req: ConversationCreateRequest) -> Dict[str, Any]:
    if req.workspace_id and not research_repository.get_workspace(req.workspace_id): raise _not_found("workspace")
    return research_repository.create_conversation(req.client_id,req.workspace_id,req.title.strip() or "新对话")


@app.get("/conversations")
def list_conversations(client_id: str,limit: int = 100) -> List[Dict[str, Any]]:
    if not 8<=len(client_id)<=128: raise HTTPException(400,detail={"code":"invalid_client_id","message":"client_id length must be 8..128"})
    return research_repository.list_conversations(client_id,limit)


@app.get("/conversations/{conversation_id}/messages")
def list_conversation_messages(conversation_id: str,client_id: str,limit: int = 200) -> List[Dict[str, Any]]:
    messages=research_repository.list_messages(conversation_id,client_id,limit)
    if messages is None: raise _not_found("conversation")
    return messages


@app.delete("/conversations/{conversation_id}",status_code=204)
def delete_conversation(conversation_id: str,client_id: str) -> Response:
    if not research_repository.delete_conversation(conversation_id,client_id): raise _not_found("conversation")
    return Response(status_code=204)


@app.delete("/workspaces/{workspace_id}", status_code=204)
def delete_workspace(workspace_id: str) -> Response:
    ws=research_repository.get_workspace(workspace_id)
    if not ws: raise _not_found("workspace")
    if research_repository.has_active_jobs(workspace_id):
        raise HTTPException(409,detail={"code":"workspace_busy","message":"cancel or finish active research jobs before deleting workspace"})
    # 级联删除数据库记录；上传文件位于独立空间目录并一并清理。
    research_repository.delete_workspace(workspace_id)
    research_executor.invalidate_workspace(workspace_id)
    shutil.rmtree(Path("data/research/uploads")/workspace_id,ignore_errors=True)
    return Response(status_code=204)


@app.get("/workspaces/{workspace_id}/documents")
def list_documents(workspace_id: str) -> List[Dict[str, Any]]:
    if not research_repository.get_workspace(workspace_id): raise _not_found("workspace")
    return research_repository.list_documents(workspace_id)


@app.post("/workspaces/{workspace_id}/documents", status_code=201)
def upload_document(workspace_id: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    if not research_repository.get_workspace(workspace_id): raise _not_found("workspace")
    raw_name=file.filename or ""
    if "/" in raw_name or "\\" in raw_name: raise HTTPException(400,detail={"code":"invalid_file","message":"path components are forbidden"})
    original=Path(raw_name).name
    safe=re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]","_",original)
    suffix=Path(safe).suffix.lower(); allowed={".pdf",".docx",".xlsx",".csv",".pptx",".txt",".doc",".xls",".ppt"}
    if not safe or safe in {".",".."} or suffix not in allowed: raise HTTPException(400,detail={"code":"invalid_file","message":"unsupported or unsafe filename"})
    max_bytes=int(os.getenv("RAG_UPLOAD_MAX_BYTES",str(20*1024*1024))); content=file.file.read(max_bytes+1)
    if len(content)>max_bytes: raise HTTPException(413,detail={"code":"file_too_large","message":f"maximum {max_bytes} bytes"})
    document_id=uuid.uuid4().hex; folder=Path("data/research/uploads")/workspace_id/document_id; folder.mkdir(parents=True,exist_ok=True); target=folder/safe
    target.write_bytes(content); now=utc_now()
    ingest_started=time.perf_counter()
    try:
        _validate_document_container(target,suffix)
        pages=ingest_document(str(target),document_id,image_output_dir=str(folder/"pages"))
        max_pages=int(os.getenv("RAG_UPLOAD_MAX_PAGES","500"))
        if len(pages)>max_pages: raise ValueError(f"document exceeds maximum {max_pages} pages")
        for p in pages: p.metadata["workspace_id"]=workspace_id; p.metadata["untrusted_context"]="true"
        payloads=[p.__dict__ for p in pages]; status,error="ready",""
    except HTTPException:
        shutil.rmtree(folder,ignore_errors=True)
        DOCUMENT_INGEST.labels(status="rejected",type=suffix.lstrip(".")).inc()
        raise
    except Exception as exc:
        payloads=[]; status,error="failed",str(exc)[:500]
    doc={"document_id":document_id,"workspace_id":workspace_id,"file_name":safe,"source_path":str(target),"content_type":file.content_type or suffix,"status":status,"page_count":len(payloads),"error_message":error,"created_at":now,"updated_at":utc_now()}
    try:
        research_repository.add_document(doc,payloads)
        research_executor.invalidate_workspace(workspace_id)
    except Exception:
        shutil.rmtree(folder,ignore_errors=True)
        raise
    DOCUMENT_INGEST.labels(status=status,type=suffix.lstrip(".")).inc()
    DOCUMENT_INGEST_DURATION.labels(type=suffix.lstrip(".")).observe(time.perf_counter()-ingest_started)
    if status=="failed": raise HTTPException(422,detail={"code":"ingest_failed","message":error})
    return doc


@app.delete("/workspaces/{workspace_id}/documents/{document_id}", status_code=204)
def delete_document(workspace_id: str, document_id: str) -> Response:
    if research_repository.has_active_jobs(workspace_id):
        raise HTTPException(409,detail={"code":"workspace_busy","message":"cancel or finish active research jobs before deleting documents"})
    path=research_repository.delete_document(workspace_id,document_id)
    if path is None: raise _not_found("document")
    research_executor.invalidate_workspace(workspace_id)
    shutil.rmtree(Path(path).parent,ignore_errors=True); return Response(status_code=204)


@app.post("/research/jobs", status_code=202)
def submit_research(req: ResearchJobRequest) -> Dict[str, Any]:
    if not research_repository.get_workspace(req.workspace_id): raise _not_found("workspace")
    job=ResearchJob(job_id=uuid.uuid4().hex,workspace_id=req.workspace_id,session_id=req.session_id,objective=req.objective.strip(),idempotency_key=req.idempotency_key)
    payload,created=research_repository.create_job(to_dict(job))
    if not created: return payload
    research_repository.append_event(job.job_id,"job_created","pending","研究任务已进入队列",0,{"objective":job.objective[:300]})
    if not research_dispatcher.submit(job.job_id):
        payload.update(status="failed",error_message="research dispatcher queue is full",finished_at=utc_now())
        research_repository.save_job(payload)
        raise HTTPException(503,detail={"code":"dispatcher_busy","message":"research queue is full; retry later"})
    RESEARCH_JOBS.labels(status="submitted").inc(); return payload


@app.get("/research/jobs/{job_id}")
def get_research_job(job_id: str) -> Dict[str, Any]:
    job=research_repository.get_job(job_id)
    if not job: raise _not_found("research_job")
    return job


@app.get("/research/jobs/{job_id}/events")
async def stream_research_events(job_id: str,request: Request,after: int = 0) -> StreamingResponse:
    if not research_repository.get_job(job_id): raise _not_found("research_job")
    last_header=request.headers.get("last-event-id","")
    cursor=max(after,int(last_header) if last_header.isdigit() else 0)
    async def generate():
        nonlocal cursor
        idle=0
        while True:
            if await request.is_disconnected(): break
            events=research_repository.list_events(job_id,cursor,200)
            if events:
                idle=0
                for event in events:
                    cursor=event["sequence_no"]
                    yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {json.dumps(event,ensure_ascii=False)}\n\n"
            else:
                idle+=1
                job=research_repository.get_job(job_id)
                if job and job["status"] in {"completed","failed","cancelled"} and idle>=2: break
                if idle%15==0: yield ": keep-alive\n\n"
            await asyncio.sleep(.35)
    return StreamingResponse(generate(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})


@app.post("/research/jobs/{job_id}/cancel")
def cancel_research_job(job_id: str) -> Dict[str, Any]:
    if not research_repository.get_job(job_id): raise _not_found("research_job")
    if not research_repository.request_cancel(job_id): raise HTTPException(409,detail={"code":"state_conflict","message":"job can no longer be cancelled"})
    RESEARCH_JOBS.labels(status="cancelled").inc()
    research_repository.append_event(job_id,"job_cancelled","cancelled","研究任务已取消",research_repository.get_job(job_id).get("progress",0))
    return research_repository.get_job(job_id) or {}


def _report_or_409(job_id: str) -> Dict[str, Any]:
    job=research_repository.get_job(job_id)
    if not job: raise _not_found("research_job")
    if job.get("status") != "completed": raise HTTPException(409,detail={"code":"report_not_ready","message":"report is only available for completed jobs"})
    report=research_repository.get_report(job_id)
    if not report: raise HTTPException(409,detail={"code":"report_not_ready","message":"report is not ready"})
    return report


@app.get("/research/jobs/{job_id}/report")
def get_report(job_id: str) -> Dict[str, Any]: return _report_or_409(job_id)


@app.get("/research/jobs/{job_id}/report.md")
def get_report_markdown(job_id: str) -> Response:
    report=_report_or_409(job_id); return Response(report["markdown_content"],media_type="text/markdown",headers={"Content-Disposition":f"attachment; filename={job_id}.md"})


@app.get("/research/jobs/{job_id}/report.html")
def get_report_html(job_id: str) -> Response:
    return Response(
        _report_or_409(job_id)["html_content"],
        media_type="text/html",
        headers={
            "Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'",
            "X-Content-Type-Options":"nosniff",
        },
    )


@app.get("/metrics")
def metrics() -> Response:
    """Prometheus 指标接口。"""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/eval/run", response_model=EvalRunResponse)
def eval_run(req: EvalRunRequest) -> EvalRunResponse:
    """触发离线评测并可选落盘，支持提测归档。"""
    report = run_eval_report(engine)
    EVAL_RUN_COUNT.inc()
    report_path: Optional[str] = None
    if req.persist:
        saved = save_eval_report(
            report,
            data_path=_data_path,
            sample_count=len(DEFAULT_EVAL_SAMPLES),
            tag=req.tag,
        )
        report_path = str(saved)
    return EvalRunResponse(
        created_at=datetime.now().isoformat(timespec="seconds"),
        persisted=req.persist,
        report_path=report_path,
        summary=report.overall.as_percent(),
        per_category=[
            {
                "category": x.category,
                "sample_count": x.sample_count,
                "recall_at_10": x.recall_at_10,
                "accuracy": x.accuracy,
                "router_acc": x.router_acc,
                "verifier_pass_rate": x.verifier_pass_rate,
                "fallback_rate": x.fallback_rate,
            }
            for x in report.per_category
        ],
        engineering={
            "sample_count": report.engineering.sample_count,
            "verifier_pass_rate": report.engineering.verifier_pass_rate,
            "fallback_rate": report.engineering.fallback_rate,
            "cache_hit_rate": report.engineering.cache_hit_rate,
            "avg_stage_latency_ms": report.engineering.avg_stage_latency_ms,
        },
    )


@app.get("/eval/last")
def eval_last() -> Dict[str, Any]:
    """读取最近一次落盘评测报告。"""
    payload = load_latest_eval_report()
    if not payload:
        return {"exists": False, "message": "no eval report found"}
    return {"exists": True, **payload}


def _ask_impl(req: AskRequest,stage_callback: Optional[Callable] = None) -> AskResponse:
    """核心问答实现；同步 JSON 与 SSE 入口共用，避免两套行为漂移。"""
    start = time.perf_counter()
    loop_steps: Optional[List[Dict[str, Any]]] = None
    sid = req.session_id
    response_engine = engine
    conversation=None
    if req.conversation_id:
        if not req.client_id: raise HTTPException(400,detail={"code":"client_id_required","message":"client_id is required with conversation_id"})
        conversation=research_repository.get_conversation(req.conversation_id,req.client_id)
        if not conversation: raise _not_found("conversation")
        if conversation.get("workspace_id") and req.workspace_id and conversation["workspace_id"]!=req.workspace_id:
            raise HTTPException(409,detail={"code":"workspace_conflict","message":"conversation belongs to another workspace"})
        research_repository.add_message(req.conversation_id,"user",req.query,{"topk":req.topk,"workspace_id":req.workspace_id})
    effective_workspace=req.workspace_id or (conversation.get("workspace_id") if conversation else None)
    try:
        if effective_workspace:
            if not research_repository.get_workspace(effective_workspace): raise _not_found("workspace")
            scoped_engine=research_executor.build_workspace_engine(effective_workspace)
            response_engine=scoped_engine
            result=scoped_engine.ask(req.query,topk=req.topk or SETTINGS.topk_default,session_id=sid,event_callback=stage_callback)
        elif SETTINGS.enable_plan_execute_loop:
            loop_run = agent_loop.run(req.query, topk=req.topk or SETTINGS.topk_default, session_id=sid,event_callback=stage_callback)
            result = loop_run.result
            loop_steps = [
                {
                    "step_no": s.step_no,
                    "plan": s.plan,
                    "branch": s.branch,
                    "verified": s.verified,
                    "hit_count": s.hit_count,
                }
                for s in loop_run.steps
            ]
        else:
            if req.topk:
                result = engine.ask(req.query, topk=req.topk, session_id=sid,event_callback=stage_callback)
            else:
                result = engine.ask(req.query, session_id=sid,event_callback=stage_callback)
    except Exception as exc:
        if SETTINGS.sentry_dsn:
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("component", "api")
                scope.set_tag("endpoint", "/ask")
                scope.set_tag("session_id", sid)
                sentry_sdk.capture_exception(exc)
        raise
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    if result.retry_hits:
        FALLBACK_COUNT.inc()
    if result.branch == "cache_hit":
        CACHE_HIT_COUNT.inc()
    VERIFY_COUNT.labels(verified=str(result.verified).lower()).inc()
    REQ_COUNT.labels(branch=result.branch, verified=str(result.verified).lower()).inc()
    REQ_LATENCY.observe(elapsed_ms / 1000.0)
    if result.trace:
        RETRY_REASON_COUNT.labels(reason=result.trace.retry_reason or "none").inc()
        ROUTER_COUNT.labels(route_branch=result.trace.route_branch, final_branch=result.branch).inc()
        for st in result.trace.stages:
            STAGE_LATENCY.labels(stage=st.stage).observe(max(st.elapsed_ms, 0) / 1000.0)

    def _hit_payload(hit) -> Dict[str, Any]:
        page = response_engine.retriever.get_page(hit.page_id)
        meta = page.metadata or {}
        title = meta.get("title") or meta.get("sheet_name") or ""
        source_file = Path(page.source_file).name if page.source_file else page.doc_id
        return {
            "page_id": hit.page_id,
            "score": hit.score,
            "doc_id": page.doc_id,
            "source_file": source_file,
            "page_no": page.page_no,
            "title": title,
        }

    response=AskResponse(
        answer=result.answer,
        branch=result.branch,
        verified=result.verified,
        rewritten_query=result.rewritten_query,
        hits=[_hit_payload(h) for h in result.hits],
        retry_hits=[_hit_payload(h) for h in result.retry_hits] if result.retry_hits else None,
        loop_steps=loop_steps,
        trace=(
            {
                "route_branch": result.trace.route_branch,
                "fallback_triggered": result.trace.fallback_triggered,
                "retry_reason": result.trace.retry_reason,
                "stages": [
                    {
                        "stage": s.stage,
                        "elapsed_ms": s.elapsed_ms,
                        "detail": s.detail,
                    }
                    for s in result.trace.stages
                ],
            }
            if result.trace
            else None
        ),
        cost_ms=elapsed_ms,
        source_files=result.source_files,
        citations=result.citation_details,
        conversation_id=req.conversation_id,
    )
    if req.conversation_id:
        payload = response.model_dump() if hasattr(response, "model_dump") else response.dict()
        research_repository.add_message(req.conversation_id,"assistant",response.answer,payload)
    return response


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    """兼容旧客户端的同步问答接口。"""
    return _ask_impl(req)


@app.post("/ask/stream")
async def ask_stream(req: AskRequest,request: Request) -> StreamingResponse:
    """普通问答 SSE：实时推送路由、检索、生成、校验和重试阶段。"""
    stage_messages={
        "cache_hit":"命中会话缓存",
        "route":"已完成意图分析与工具路由",
        "retrieve":"已完成知识库检索",
        "agentic_critique":"证据不足，Agent 正在反思并改写检索",
        "retry_retrieve":"已完成扩展检索",
        "generate":"已完成答案生成",
        "verify":"已完成证据校验",
        "retry_generate":"已完成重试生成",
        "retry_verify":"已完成重试校验",
    }
    async def generate():
        loop=asyncio.get_running_loop(); events: asyncio.Queue = asyncio.Queue()
        def on_stage(stage) -> None:
            payload={
                "stage":stage.stage,
                "message":stage_messages.get(stage.stage,stage.stage),
                "elapsed_ms":stage.elapsed_ms,
                "detail":stage.detail,
            }
            loop.call_soon_threadsafe(events.put_nowait,payload)
        yield "event: accepted\ndata: "+json.dumps({"stage":"accepted","message":"已接收问题，开始执行 Agent"},ensure_ascii=False)+"\n\n"
        task=asyncio.create_task(asyncio.to_thread(_ask_impl,req,on_stage))
        while not task.done() or not events.empty():
            if await request.is_disconnected():
                break
            try:
                item=await asyncio.wait_for(events.get(),timeout=.25)
                yield "event: stage\ndata: "+json.dumps(item,ensure_ascii=False)+"\n\n"
            except asyncio.TimeoutError:
                continue
        if await request.is_disconnected(): return
        try:
            response=await task
            payload=response.model_dump() if hasattr(response,"model_dump") else response.dict()
            yield "event: final\ndata: "+json.dumps(payload,ensure_ascii=False)+"\n\n"
        except Exception as exc:
            yield "event: error\ndata: "+json.dumps({"message":str(exc)[:500]},ensure_ascii=False)+"\n\n"
    return StreamingResponse(generate(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
