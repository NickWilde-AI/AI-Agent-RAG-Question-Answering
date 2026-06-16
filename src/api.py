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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import FileResponse, Response
import sentry_sdk

from .bootstrap import build_agent_loop, build_engine
from .config import SETTINGS
from .middleware_ops import RateLimitMiddleware, RequestTimingMiddleware
from .router import router_circuit_open
from .services import vlm_circuit_open
from .eval_suite import DEFAULT_EVAL_SAMPLES, run_eval_report
from .infra.eval_report_store import load_latest_eval_report, save_eval_report

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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
_user_pages = Path("data/user_pages.json")
_data_path = str(_user_pages) if _user_pages.exists() else "data/demo_pages.json"
engine = build_engine(_data_path)
agent_loop = build_agent_loop(_data_path)


class AskRequest(BaseModel):
    """问答请求模型。"""

    query: str = Field(..., min_length=1, description="用户问题")
    session_id: str = Field(default="default", min_length=1, max_length=128, description="会话 ID")
    topk: Optional[int] = Field(default=None, ge=1, le=20, description="可选 top-k")


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
        "milvus": SETTINGS.vector_backend == "milvus",
        "colpali_rerank": bool(SETTINGS.enable_colpali_rerank and SETTINGS.colpali_rerank_api),
        "function_calling_router": bool(SETTINGS.enable_function_calling_router and SETTINGS.openai_api_key),
        "langgraph": SETTINGS.enable_langgraph,
        "vlm": bool(SETTINGS.vlm_api),
        "chart_parsing": bool(SETTINGS.chart_parsing_api),
        "bm25_fallback": SETTINGS.enable_bm25_fallback,
        "rate_limit": SETTINGS.enable_rate_limit,
        "router_circuit_breaker": SETTINGS.enable_router_circuit_breaker,
        "router_circuit_open": router_circuit_open(),
        "vlm_circuit_breaker": SETTINGS.enable_vlm_circuit_breaker,
        "vlm_circuit_open": vlm_circuit_open(),
        "local_colpali_model": Path(SETTINGS.colpali_model_dir).exists(),
        "local_colpali_model_dir": SETTINGS.colpali_model_dir,
        "redis_memory": SETTINGS.session_backend == "redis",
        "session_cache": SETTINGS.enable_session_cache,
        "agentic_query_expansion": SETTINGS.enable_query_expansion,
        "agentic_retry_refine": SETTINGS.enable_agentic_retry_refine,
        "benchmark": {
            "recall_at_10": SETTINGS.benchmark_recall_at_10,
            "accuracy": SETTINGS.benchmark_accuracy,
            "router_accuracy": SETTINGS.benchmark_router_accuracy,
        },
    }


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


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    """核心问答接口。"""
    start = time.perf_counter()
    loop_steps: Optional[List[Dict[str, Any]]] = None
    sid = req.session_id
    try:
        if SETTINGS.enable_plan_execute_loop:
            loop_run = agent_loop.run(req.query, topk=req.topk or SETTINGS.topk_default, session_id=sid)
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
                result = engine.ask(req.query, topk=req.topk, session_id=sid)
            else:
                result = engine.ask(req.query, session_id=sid)
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
        page = engine.retriever.get_page(hit.page_id)
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

    return AskResponse(
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
    )
