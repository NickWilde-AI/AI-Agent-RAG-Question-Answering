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
from typing import Optional

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


def _get_optional_float(name: str) -> Optional[float]:
    value = os.getenv(name, "").strip()
    return float(value) if value else None


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
    # Agentic retrieval 轻量版：把复杂问题拆成多个检索意图，再用 RRF 融合，减少单 query 漏召回。
    enable_query_expansion: bool = _get_bool("RAG_ENABLE_QUERY_EXPANSION", True)
    max_query_variants: int = int(os.getenv("RAG_MAX_QUERY_VARIANTS", "4"))
    rrf_k: int = int(os.getenv("RAG_RRF_K", "60"))
    retrieval_diversity_per_doc: int = int(os.getenv("RAG_RETRIEVAL_DIVERSITY_PER_DOC", "3"))
    # 大库检索：先保留较大的粗召回集合，再由可插拔视觉模型重排，最终才截断到业务 top-k。
    enable_hybrid_bm25: bool = _get_bool("RAG_ENABLE_HYBRID_BM25", True)
    enable_hierarchical_retrieval: bool = _get_bool("RAG_ENABLE_HIERARCHICAL_RETRIEVAL", True)
    retrieval_candidate_pages: int = int(os.getenv("RAG_RETRIEVAL_CANDIDATE_PAGES", "30"))
    retrieval_candidate_docs: int = int(os.getenv("RAG_RETRIEVAL_CANDIDATE_DOCS", "20"))
    enable_visual_rerank: bool = _get_bool("RAG_ENABLE_VISUAL_RERANK", False)
    visual_rerank_candidate_pages: int = int(os.getenv("RAG_VISUAL_RERANK_CANDIDATE_PAGES", "8"))
    visual_rerank_weight: float = float(os.getenv("RAG_VISUAL_RERANK_WEIGHT", "0.65"))
    enable_agentic_retry_refine: bool = _get_bool("RAG_ENABLE_AGENTIC_RETRY_REFINE", True)
    agentic_retry_max_missing_terms: int = int(os.getenv("RAG_AGENTIC_RETRY_MAX_MISSING_TERMS", "6"))
    enable_llm_router: bool = _get_bool("RAG_ENABLE_LLM_ROUTER", False)
    enable_llm_verifier: bool = _get_bool("RAG_ENABLE_LLM_VERIFIER", False)
    # 默认关闭：大库逐页真实 embedding 会长时间占满 CPU/网络，易把机器拖死
    enable_real_embedding: bool = _get_bool("RAG_ENABLE_REAL_EMBEDDING", False)
    enable_multimodal_embedding: bool = _get_bool("RAG_ENABLE_MULTIMODAL_EMBEDDING", False)
    enable_colpali_rerank: bool = _get_bool("RAG_ENABLE_COLPALI_RERANK", False)
    enable_function_calling_router: bool = _get_bool("RAG_ENABLE_FUNCTION_CALLING_ROUTER", True)
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    dashscope_api_key: str = os.getenv("DASHSCOPE_API_KEY", "")
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
    colpali_rerank_max_pages: int = int(os.getenv("RAG_COLPALI_RERANK_MAX_PAGES", "20"))
    vlm_api: str = os.getenv("RAG_VLM_API", "")
    chart_parsing_api: str = os.getenv("RAG_CHART_PARSING_API", "")
    # 文档入库阶段：复用 DashScope OpenAI-compatible 地址和 Key，调用千问 VL 解析页图。
    enable_qwen_vision_parser: bool = _get_bool("RAG_ENABLE_QWEN_VISION_PARSER", False)
    vision_parser_model: str = os.getenv("RAG_VISION_PARSER_MODEL", "qwen-vl-ocr")
    qwen_vlm_model: str = os.getenv("RAG_QWEN_VLM_MODEL", "qwen3-vl-plus")
    qwen_vlm_verifier_model: str = os.getenv("RAG_QWEN_VLM_VERIFIER_MODEL", "qwen3-vl-flash")
    qwen_vlm_rerank_model: str = os.getenv("RAG_QWEN_VLM_RERANK_MODEL", "qwen3-vl-flash")
    vision_parse_mode: str = os.getenv("RAG_VISION_PARSE_MODE", "auto").strip().lower()
    vision_parser_workers: int = int(os.getenv("RAG_VISION_PARSER_WORKERS", "3"))
    vision_parser_timeout_seconds: float = float(os.getenv("RAG_VISION_PARSER_TIMEOUT_SECONDS", "60"))
    vision_min_text_chars: int = int(os.getenv("RAG_VISION_MIN_TEXT_CHARS", "200"))
    vision_drawing_threshold: int = int(os.getenv("RAG_VISION_DRAWING_THRESHOLD", "12"))
    vision_office_to_pdf: bool = _get_bool("RAG_VISION_OFFICE_TO_PDF", True)
    enable_embedding_cache: bool = _get_bool("RAG_ENABLE_EMBEDDING_CACHE", True)
    embedding_cache_path: str = os.getenv("RAG_EMBEDDING_CACHE_PATH", "data/embedding_cache.json")
    embedding_cache_max_entries: int = int(os.getenv("RAG_EMBEDDING_CACHE_MAX_ENTRIES", "20000"))

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
    research_tool_timeout_seconds: float = float(os.getenv("RAG_RESEARCH_TOOL_TIMEOUT_SECONDS", "30"))
    research_job_timeout_seconds: float = float(os.getenv("RAG_RESEARCH_JOB_TIMEOUT_SECONDS", "300"))
    research_dispatch_workers: int = int(os.getenv("RAG_RESEARCH_DISPATCH_WORKERS", "2"))
    research_dispatch_queue: int = int(os.getenv("RAG_RESEARCH_DISPATCH_QUEUE", "32"))
    research_engine_cache_size: int = int(os.getenv("RAG_RESEARCH_ENGINE_CACHE_SIZE", "16"))
    # 企业边界默认关闭，开启后 Workspace 必须通过 JWT + ACL 授权。
    enable_auth: bool = _get_bool("RAG_ENABLE_AUTH", False)
    jwt_secret: str = os.getenv("RAG_JWT_SECRET", "")
    jwt_issuer: str = os.getenv("RAG_JWT_ISSUER", "")
    jwt_audience: str = os.getenv("RAG_JWT_AUDIENCE", "")
    # 高风险 Skill(合同/发票、HR 招聘)要求的角色；启用鉴权后，缺少该角色的用户被拒。
    # 逗号分隔，命中任一即放行；admin 始终放行。留空表示不额外限制。
    agent_center_high_risk_roles: str = os.getenv("RAG_AGENT_HIGH_RISK_ROLES", "hr,finance,analyst,admin")
    # 研究任务的 Planner / Executor / Verifier 多角色 LangGraph 编排。
    enable_research_langgraph: bool = _get_bool("RAG_ENABLE_RESEARCH_LANGGRAPH", False)
    cors_origins: str = os.getenv("RAG_CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000")
    benchmark_recall_at_10: Optional[float] = _get_optional_float("RAG_BENCHMARK_RECALL_AT_10")
    benchmark_accuracy: Optional[float] = _get_optional_float("RAG_BENCHMARK_ACCURACY")
    benchmark_router_accuracy: Optional[float] = _get_optional_float("RAG_BENCHMARK_ROUTER_ACCURACY")
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

    @property
    def effective_openai_api_key(self) -> str:
        if "dashscope.aliyuncs.com" in self.openai_base_url and self.dashscope_api_key:
            return self.dashscope_api_key
        return self.openai_api_key


SETTINGS = Settings()
