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

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import FileResponse, Response

from .bootstrap import build_agent_loop, build_engine
from .config import SETTINGS

# 指标保持最小集合：总请求数 + 耗时 + 回退次数
REQ_COUNT = Counter("rag_requests_total", "Total requests", ["branch", "verified"])
REQ_LATENCY = Histogram("rag_request_latency_seconds", "Request latency seconds")
FALLBACK_COUNT = Counter("rag_fallback_total", "Fallback retries triggered")

app = FastAPI(title="Visual RAG Agent API", version="0.1.0")
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
    cost_ms: int
    source_files: List[str] = Field(default_factory=list, description="本次回答依据的源文件完整文件名（去重）")


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
        "vlm": bool(SETTINGS.vlm_api),
        "chart_parsing": bool(SETTINGS.chart_parsing_api),
        "google_translate": bool(SETTINGS.google_translate_api and SETTINGS.google_translate_api_key),
        "deepl": bool(SETTINGS.deepl_api_key),
        "local_colpali_model": Path(SETTINGS.colpali_model_dir).exists(),
        "local_colpali_model_dir": SETTINGS.colpali_model_dir,
        "redis_memory": SETTINGS.session_backend == "redis",
        "benchmark": {
            "recall_at_10": SETTINGS.benchmark_recall_at_10,
            "accuracy": SETTINGS.benchmark_accuracy,
            "router_accuracy": SETTINGS.benchmark_router_accuracy,
            "translate_general_accuracy": SETTINGS.benchmark_translate_general_accuracy,
            "translate_domain_accuracy": SETTINGS.benchmark_translate_domain_accuracy,
        },
    }


@app.get("/metrics")
def metrics() -> Response:
    """Prometheus 指标接口。"""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    """核心问答接口。"""
    start = time.perf_counter()
    loop_steps: Optional[List[Dict[str, Any]]] = None
    if SETTINGS.enable_plan_execute_loop:
        loop_run = agent_loop.run(req.query, topk=req.topk or SETTINGS.topk_default)
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
        result = engine.ask(req.query, topk=req.topk) if req.topk else engine.ask(req.query)
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    if result.retry_hits:
        FALLBACK_COUNT.inc()
    REQ_COUNT.labels(branch=result.branch, verified=str(result.verified).lower()).inc()
    REQ_LATENCY.observe(elapsed_ms / 1000.0)

    return AskResponse(
        answer=result.answer,
        branch=result.branch,
        verified=result.verified,
        rewritten_query=result.rewritten_query,
        hits=[{"page_id": h.page_id, "score": h.score} for h in result.hits],
        retry_hits=[{"page_id": h.page_id, "score": h.score} for h in result.retry_hits] if result.retry_hits else None,
        loop_steps=loop_steps,
        cost_ms=elapsed_ms,
        source_files=result.source_files,
    )
