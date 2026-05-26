"""
config.py — 全局配置（环境变量 → 一个只读 Settings 对象）

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
这里是「开关面板」：决定 top-k、是否走 LLM Router/Verifier、向量后端 Milvus 还是内存、
是否开启外层 Plan-Execute Loop 等。不直接参与业务编排，但影响 pipeline/agent_loop 行为。

================================================================================
【类比 Android / Java 后端】
================================================================================
- 像 `BuildConfig` + `gradle.properties` + 远程配置的合体：集中读环境，避免魔法数散落在 Activity。
- `SETTINGS = Settings()` 单例：类似 Kotlin `object AppConfig` 或 Java `public static final` 配置 holder。
- `frozen=True` 的 dataclass：像「不可变配置 DTO」，构造后字段不应被改，减少并发/误改隐患。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `@dataclass(frozen=True)`：为类生成 `__init__`、`__repr__` 等；`frozen=True` 等价于字段只读（赋值会抛错）。
- `os.getenv("KEY", "default")`：类似 `System.getenv()`，第二个参数是默认值字符串。
- `bool: _get_bool(...)`：把字符串 `"1" / true / yes / on"` 解析成布尔，等价于手写 `Boolean.parseBoolean` 的宽松版。
- `Path(__file__).resolve().parent.parent`：`__file__` 是当前源文件路径；`.parent` 上一级目录；用来定位仓库根下的 `.env`。
- `try/except ImportError`：可选依赖 `python-dotenv` 没装时跳过，不阻塞启动（类似 Gradle optional module）。
- 文件末尾 `SETTINGS = Settings()`：模块 import 时执行一次，全局单例（注意：单测里若要换配置需 mock 或 reload）。

生产风格配置模块（支持环境变量）。
"""

from dataclasses import dataclass
import os
from pathlib import Path

# 从仓库根目录 .env 注入环境变量（无需依赖 shell 里先 source .env）
try:
    from dotenv import load_dotenv

    _ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(_ENV_FILE)
except ImportError:
    pass


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_openai_base_url(value: str) -> str:
    """兼容用户直接填入 /chat/completions 完整地址的情况。"""
    normalized = value.strip()
    if normalized.endswith("/chat/completions"):
        return normalized[: -len("/chat/completions")]
    return normalized


@dataclass(frozen=True)
class Settings:
    """全局设置（可通过环境变量覆盖）。"""

    topk_default: int = int(os.getenv("RAG_TOPK_DEFAULT", "3"))
    topk_retry_multiplier: int = int(os.getenv("RAG_TOPK_RETRY_MULTIPLIER", "2"))
    topk_fact: int = int(os.getenv("RAG_TOPK_FACT", "3"))
    topk_multi_page: int = int(os.getenv("RAG_TOPK_MULTI_PAGE", "5"))
    topk_chart: int = int(os.getenv("RAG_TOPK_CHART", "4"))
    max_retry_topk: int = int(os.getenv("RAG_MAX_RETRY_TOPK", "12"))
    enable_branch_fallback: bool = _get_bool("RAG_ENABLE_BRANCH_FALLBACK", True)
    vector_dim: int = int(os.getenv("RAG_VECTOR_DIM", "256"))
    enable_query_rewrite: bool = _get_bool("RAG_ENABLE_QUERY_REWRITE", True)
    enable_llm_router: bool = _get_bool("RAG_ENABLE_LLM_ROUTER", False)
    enable_llm_verifier: bool = _get_bool("RAG_ENABLE_LLM_VERIFIER", False)
    # 默认关闭：大库逐页真实 embedding 会长时间占满 CPU/网络，易把机器拖死
    enable_real_embedding: bool = _get_bool("RAG_ENABLE_REAL_EMBEDDING", False)
    enable_multimodal_embedding: bool = _get_bool("RAG_ENABLE_MULTIMODAL_EMBEDDING", False)
    enable_colpali_rerank: bool = _get_bool("RAG_ENABLE_COLPALI_RERANK", False)
    enable_function_calling_router: bool = _get_bool("RAG_ENABLE_FUNCTION_CALLING_ROUTER", True)
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    oapi_api_key: str = os.getenv("OAPI_API_KEY", "")
    openai_base_url: str = _normalize_openai_base_url(os.getenv("OPENAI_BASE_URL", ""))
    openai_chat_model: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
    openai_embedding_model: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    llm_max_retries: int = int(os.getenv("RAG_LLM_MAX_RETRIES", "2"))
    llm_retry_base_seconds: float = float(os.getenv("RAG_LLM_RETRY_BASE_SECONDS", "0.6"))
    llm_retry_max_seconds: float = float(os.getenv("RAG_LLM_RETRY_MAX_SECONDS", "4.0"))
    multimodal_embedding_api: str = os.getenv("RAG_MULTIMODAL_EMBEDDING_API", "")
    colpali_rerank_api: str = os.getenv("RAG_COLPALI_RERANK_API", "")
    colpali_rerank_timeout_seconds: float = float(os.getenv("RAG_COLPALI_RERANK_TIMEOUT_SECONDS", "12"))
    colpali_rerank_max_pages: int = int(os.getenv("RAG_COLPALI_RERANK_MAX_PAGES", "6"))
    vlm_api: str = os.getenv("RAG_VLM_API", "")
    chart_parsing_api: str = os.getenv("RAG_CHART_PARSING_API", "")
    external_api_timeout_seconds: float = float(os.getenv("RAG_EXTERNAL_API_TIMEOUT_SECONDS", "10"))
    colpali_model_id: str = os.getenv("COLPALI_MODEL_ID", "vidore/colpali-v1.3")
    colpali_model_dir: str = os.getenv("COLPALI_MODEL_DIR", "models/colpali-v1.3")
    # ReAct 思路开关：启用后会走 plan-execute-verify-retry loop（默认关，减轻延迟与负载）
    enable_plan_execute_loop: bool = _get_bool("RAG_ENABLE_PLAN_EXECUTE_LOOP", False)
    # 启用 LangGraph 主链路编排（默认关闭，保持原有自研状态机行为）
    enable_langgraph: bool = _get_bool("RAG_ENABLE_LANGGRAPH", False)
    # 向量库后端：inmemory / milvus
    vector_backend: str = os.getenv("RAG_VECTOR_BACKEND", "inmemory")
    milvus_uri: str = os.getenv("MILVUS_URI", "http://localhost:19530")
    milvus_token: str = os.getenv("MILVUS_TOKEN", "")
    milvus_collection: str = os.getenv("MILVUS_COLLECTION", "rag_pages")
    # 会话缓存后端：memory / redis
    session_backend: str = os.getenv("RAG_SESSION_BACKEND", "memory")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_ttl_seconds: int = int(os.getenv("RAG_REDIS_TTL_SECONDS", "1800"))
    enable_session_cache: bool = _get_bool("RAG_ENABLE_SESSION_CACHE", True)
    session_cache_require_verified: bool = _get_bool("RAG_SESSION_CACHE_REQUIRE_VERIFIED", True)
    session_max_history: int = int(os.getenv("RAG_SESSION_MAX_HISTORY", "50"))
    benchmark_recall_at_10: float = float(os.getenv("RAG_BENCHMARK_RECALL_AT_10", "0.894"))
    benchmark_accuracy: float = float(os.getenv("RAG_BENCHMARK_ACCURACY", "0.587"))
    benchmark_router_accuracy: float = float(os.getenv("RAG_BENCHMARK_ROUTER_ACCURACY", "0.92"))
    # 降级检索：外部 embedding 不可用时可切到 BM25 词面召回
    enable_bm25_fallback: bool = _get_bool("RAG_ENABLE_BM25_FALLBACK", True)
    bm25_k1: float = float(os.getenv("RAG_BM25_K1", "1.2"))
    bm25_b: float = float(os.getenv("RAG_BM25_B", "0.75"))
    # 可观测/告警
    sentry_dsn: str = os.getenv("SENTRY_DSN", "")
    # HTTP 限流（/ask）
    enable_rate_limit: bool = _get_bool("RAG_ENABLE_RATE_LIMIT", True)
    rate_limit_rps: float = float(os.getenv("RAG_RATE_LIMIT_RPS", "5"))
    rate_limit_burst: float = float(os.getenv("RAG_RATE_LIMIT_BURST", "10"))
    # Router LLM 熔断（失败后走规则路由）
    enable_router_circuit_breaker: bool = _get_bool("RAG_ENABLE_ROUTER_CIRCUIT_BREAKER", True)
    router_cb_failures: int = int(os.getenv("RAG_ROUTER_CB_FAILURES", "5"))
    router_cb_recovery_seconds: float = float(os.getenv("RAG_ROUTER_CB_RECOVERY_SECONDS", "30"))
    # VLM 熔断（连续失败后短时间禁用 VLM，降级到文本链路）
    enable_vlm_circuit_breaker: bool = _get_bool("RAG_ENABLE_VLM_CIRCUIT_BREAKER", True)
    vlm_cb_failures: int = int(os.getenv("RAG_VLM_CB_FAILURES", "3"))
    vlm_cb_recovery_seconds: float = float(os.getenv("RAG_VLM_CB_RECOVERY_SECONDS", "30"))


SETTINGS = Settings()
