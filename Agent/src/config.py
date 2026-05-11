"""生产风格配置模块（支持环境变量）。"""

from dataclasses import dataclass
import os


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
    vector_dim: int = int(os.getenv("RAG_VECTOR_DIM", "256"))
    enable_query_rewrite: bool = _get_bool("RAG_ENABLE_QUERY_REWRITE", True)
    enable_llm_router: bool = _get_bool("RAG_ENABLE_LLM_ROUTER", False)
    enable_llm_verifier: bool = _get_bool("RAG_ENABLE_LLM_VERIFIER", False)
    enable_llm_translation: bool = _get_bool("RAG_ENABLE_LLM_TRANSLATION", False)
    # 默认关闭：大库逐页真实 embedding 会长时间占满 CPU/网络，易把机器拖死
    enable_real_embedding: bool = _get_bool("RAG_ENABLE_REAL_EMBEDDING", False)
    enable_multimodal_embedding: bool = _get_bool("RAG_ENABLE_MULTIMODAL_EMBEDDING", False)
    enable_colpali_rerank: bool = _get_bool("RAG_ENABLE_COLPALI_RERANK", False)
    enable_function_calling_router: bool = _get_bool("RAG_ENABLE_FUNCTION_CALLING_ROUTER", True)
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = _normalize_openai_base_url(os.getenv("OPENAI_BASE_URL", ""))
    openai_chat_model: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1-mini")
    openai_embedding_model: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    multimodal_embedding_api: str = os.getenv("RAG_MULTIMODAL_EMBEDDING_API", "")
    colpali_rerank_api: str = os.getenv("RAG_COLPALI_RERANK_API", "")
    colpali_rerank_timeout_seconds: float = float(os.getenv("RAG_COLPALI_RERANK_TIMEOUT_SECONDS", "12"))
    colpali_rerank_max_pages: int = int(os.getenv("RAG_COLPALI_RERANK_MAX_PAGES", "6"))
    vlm_api: str = os.getenv("RAG_VLM_API", "")
    chart_parsing_api: str = os.getenv("RAG_CHART_PARSING_API", "")
    external_api_timeout_seconds: float = float(os.getenv("RAG_EXTERNAL_API_TIMEOUT_SECONDS", "10"))
    google_translate_api: str = os.getenv("GOOGLE_TRANSLATE_API", "")
    google_translate_api_key: str = os.getenv("GOOGLE_TRANSLATE_API_KEY", "")
    deepl_api: str = os.getenv("DEEPL_API", "https://api-free.deepl.com/v2/translate")
    deepl_api_key: str = os.getenv("DEEPL_API_KEY", "")
    oapi_chat_completions_url: str = os.getenv("OAPI_CHAT_COMPLETIONS_URL", "https://oapi.uk/v1/chat/completions")
    oapi_api_key: str = os.getenv("OAPI_API_KEY", "")
    oapi_translation_model: str = os.getenv("OAPI_TRANSLATION_MODEL", os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"))
    colpali_model_id: str = os.getenv("COLPALI_MODEL_ID", "vidore/colpali-v1.3")
    colpali_model_dir: str = os.getenv("COLPALI_MODEL_DIR", "models/colpali-v1.3")
    # ReAct 思路开关：启用后会走 plan-execute-verify-retry loop（默认关，减轻延迟与负载）
    enable_plan_execute_loop: bool = _get_bool("RAG_ENABLE_PLAN_EXECUTE_LOOP", False)
    # 向量库后端：inmemory / milvus
    vector_backend: str = os.getenv("RAG_VECTOR_BACKEND", "inmemory")
    milvus_uri: str = os.getenv("MILVUS_URI", "http://localhost:19530")
    milvus_token: str = os.getenv("MILVUS_TOKEN", "")
    milvus_collection: str = os.getenv("MILVUS_COLLECTION", "rag_pages")
    # 会话缓存后端：memory / redis
    session_backend: str = os.getenv("RAG_SESSION_BACKEND", "memory")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_ttl_seconds: int = int(os.getenv("RAG_REDIS_TTL_SECONDS", "1800"))
    benchmark_recall_at_10: float = float(os.getenv("RAG_BENCHMARK_RECALL_AT_10", "0.894"))
    benchmark_accuracy: float = float(os.getenv("RAG_BENCHMARK_ACCURACY", "0.587"))
    benchmark_router_accuracy: float = float(os.getenv("RAG_BENCHMARK_ROUTER_ACCURACY", "0.92"))
    benchmark_translate_general_accuracy: float = float(os.getenv("RAG_BENCHMARK_TRANSLATE_GENERAL_ACCURACY", "0.806"))
    benchmark_translate_domain_accuracy: float = float(os.getenv("RAG_BENCHMARK_TRANSLATE_DOMAIN_ACCURACY", "0.704"))


SETTINGS = Settings()
