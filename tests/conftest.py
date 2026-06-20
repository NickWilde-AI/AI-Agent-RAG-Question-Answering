"""测试收集前强制关闭外部服务，避免本地 .env 触发真实网络调用。"""

import os


os.environ.update(
    {
        "OPENAI_API_KEY": "",
        "OAPI_API_KEY": "",
        "RAG_ENABLE_LLM_ROUTER": "false",
        "RAG_ENABLE_LLM_VERIFIER": "false",
        "RAG_ENABLE_FUNCTION_CALLING_ROUTER": "false",
        "RAG_ENABLE_REAL_EMBEDDING": "false",
        "RAG_ENABLE_MULTIMODAL_EMBEDDING": "false",
        "RAG_ENABLE_COLPALI_RERANK": "false",
        "RAG_VECTOR_BACKEND": "inmemory",
        "RAG_SESSION_BACKEND": "memory",
        "RAG_ENABLE_RATE_LIMIT": "false",
        "RAG_LLM_MAX_RETRIES": "0",
    }
)
