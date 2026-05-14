"""
redis_memory.py — SessionMemory 的 Redis 实现（可选）

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
- 由 `bootstrap.build_engine` 在 `RAG_SESSION_BACKEND=redis` 时选用；失败回退内存版。
- `add_record`：先调 `super().add_record` 写内存，再 `SETEX` 把「query → page_ids」JSON 写入 Redis。
- `get_cached_pages`：先读 Redis，miss 再读父类内存映射。

================================================================================
【类比 Android】
================================================================================
- 像 **Room + MemoryCache 双层**：热数据在 Redis，进程内仍保留一份 `qa_history` 便于演示。
- `setex` ≈ 带 TTL 的 `SharedPreferences` 不合适类比，更接近 **Memcached key with expiry**。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `class RedisSessionMemory(SessionMemory)`：**继承**父类，复用 `qa_history` 字段与部分逻辑；Kotlin `class R : SessionMemory()`。
- `super().add_record(result)`：显式调父类实现；Python 3 可用 `super()` 无参数形式。
- `import redis  # type: ignore`：类型检查器跳过找不到 stub 的第三方包；`# type: ignore` 是注释指令。
- `raise RuntimeError(...) from exc`：**异常链**保留根因，类似 Java `throw new ServiceException(e)`。

Redis 会话缓存（可选依赖）。
"""

from __future__ import annotations

import json
from typing import Dict, List

from ..memory import SessionMemory
from ..models import QAResult, RetrievalHit


class RedisSessionMemory(SessionMemory):
    """
    兼容 SessionMemory 接口的 Redis 实现。

    - 失败时建议在 bootstrap 层自动回退到内存版
    - 这里只持久化 query->pages，足够演示缓存收益点
    """

    def __init__(self, redis_url: str, ttl_seconds: int = 1800) -> None:
        super().__init__()
        try:
            import redis  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "Redis backend requires redis-py. "
                "Install dependency or set RAG_SESSION_BACKEND=memory."
            ) from exc
        self._client = redis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl_seconds

    def add_record(self, result: QAResult) -> None:
        super().add_record(result)
        key = f"qa:query:{result.query}"
        page_ids = [h.page_id for h in result.hits]
        self._client.setex(key, self._ttl, json.dumps(page_ids, ensure_ascii=False))

    def get_cached_pages(self, query: str) -> List[str]:
        # 优先从 Redis 读，读不到再走内存兜底
        key = f"qa:query:{query}"
        val = self._client.get(key)
        if val:
            return list(json.loads(val))
        return super().get_cached_pages(query)

    @staticmethod
    def to_qa_result(query: str, answer: str, page_ids: List[str]) -> QAResult:
        """工具方法：将缓存命中内容转成 QAResult 的最小结构。"""
        return QAResult(
            query=query,
            rewritten_query=query,
            branch="cache_hit",
            answer=answer,
            verified=True,
            hits=[RetrievalHit(page_id=p, score=1.0) for p in page_ids],
            source_files=[],
        )

