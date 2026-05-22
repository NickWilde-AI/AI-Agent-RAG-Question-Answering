"""
RedisSessionMemory — SessionMemory 的 Redis 后端。

与 memory.SessionMemory 的关系：
  - 继承同一套语义（try_get / add_record / get_recent_history）
  - 先写/读 Redis，必要时 fallback 到父类进程内 dict（双写：add_record 会 super() 再 SETEX）

Redis 数据结构（每个 session_id 一套）：
  rag:sess:{sid}:e:{hash}   SETEX  问句级缓存条目（SessionCacheEntry JSON）
  rag:sess:{sid}:hist       LIST   最近问答（LPUSH + LTRIM）
  rag:sess:{sid}:eidx       SET    缓存 key 索引，clear_session 时批量删除

启用：RAG_SESSION_BACKEND=redis，REDIS_URL=redis://redis:6379/0（Compose 内网）。
选型：bootstrap.build_engine()。
"""

from __future__ import annotations

import hashlib
import json
from typing import List, Optional

from ..memory import SessionCacheEntry, SessionMemory, normalize_query
from ..models import QAResult


class RedisSessionMemory(SessionMemory):
    """多 API 副本共享会话；条目带 TTL（默认 30 分钟）。"""

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int = 1800,
        max_history: int = 50,
        cache_verified_only: bool = True,
    ) -> None:
        super().__init__(max_history=max_history, cache_verified_only=cache_verified_only)
        try:
            import redis  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "Redis backend requires redis-py. "
                "Install dependency or set RAG_SESSION_BACKEND=memory."
            ) from exc
        self._client = redis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl_seconds

    @staticmethod
    def _entry_key(session_id: str, norm_query: str) -> str:
        """问句缓存 key；hash 避免 key 过长或含特殊字符。"""
        digest = hashlib.sha256(norm_query.encode("utf-8")).hexdigest()[:16]
        return f"rag:sess:{session_id}:e:{digest}"

    @staticmethod
    def _hist_key(session_id: str) -> str:
        return f"rag:sess:{session_id}:hist"

    @staticmethod
    def _index_key(session_id: str) -> str:
        """记录该会话下所有 entry key，便于 clear_session。"""
        return f"rag:sess:{session_id}:eidx"

    def try_get(self, session_id: str, query: str) -> Optional[QAResult]:
        """【主链路·读】Redis GET → 失败则读父类内存 L1。"""
        norm = normalize_query(query)
        raw = self._client.get(self._entry_key(session_id, norm))
        if raw:
            try:
                return SessionCacheEntry.from_json(raw).to_qa_result()
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
        return super().try_get(session_id, query)

    def add_record(self, result: QAResult, session_id: str = "default") -> None:
        """
        【主链路·写】由 pipeline.QAEngine.ask 在每次 return 前调用。

        调用方传入的 session_id 一般对应用户 id，用于在 Redis 里隔离不同用户的抽屉。
        写入分两步：先更新本进程内存（父类），再写入 Redis（跨 API 副本共享）。
        """
        # ① 父类 SessionMemory.add_record：
        #    - _history 始终追加本轮 QAResult（供 get_recent_history）
        #    - _entries 仅在 verified 等条件满足时更新（与下面 Redis 判断一致）
        super().add_record(result, session_id=session_id)

        # ② 以下决定是否同步到 Redis（问句级「可 cache_hit 的」缓存，不是历史列表本身）
        # 若本轮本身就是缓存命中返回的，不再写回 Redis，避免无限套娃
        if result.branch == "cache_hit":
            return
        # 默认只缓存 Verifier 通过的答案，防止错误答案被固化（RAG_SESSION_CACHE_REQUIRE_VERIFIED）
        if self.cache_verified_only and not result.verified:
            return

        # ③ 构造要落盘的条目：含 answer、page_ids、source_files 等，JSON 后存入 Redis
        norm = normalize_query(result.query)  # 缓存 key 用归一化问句，不是原文大小写/空格
        entry = SessionCacheEntry.from_qa_result(result, norm)
        ek = self._entry_key(session_id, norm)  # 例如 rag:sess:user-10086:e:a1b2c3...

        # ④ 用 pipeline 一次提交多条 Redis 命令，减少往返延迟
        pipe = self._client.pipeline()

        # 问句 → 完整问答条目；TTL 到期自动删除（默认 1800s）
        pipe.setex(ek, self._ttl, entry.to_json())
        # 把 ek 记入该 session 的索引集合，clear_session 时能批量删掉所有条目 key
        pipe.sadd(self._index_key(session_id), ek)
        pipe.expire(self._index_key(session_id), self._ttl)

        # 历史列表：最新一轮 LPUSH 到列表头；LTRIM 只保留 max_history 条；同样带 TTL
        pipe.lpush(self._hist_key(session_id), entry.to_json())
        pipe.ltrim(self._hist_key(session_id), 0, self.max_history - 1)
        pipe.expire(self._hist_key(session_id), self._ttl)

        pipe.execute()

    def get_cached_pages(self, query: str, session_id: str = "default") -> List[str]:
        """只读 page_id 列表；Redis miss 时回退父类。"""
        norm = normalize_query(query)
        raw = self._client.get(self._entry_key(session_id, norm))
        if raw:
            try:
                return list(SessionCacheEntry.from_json(raw).page_ids)
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
        return super().get_cached_pages(query, session_id=session_id)

    def get_recent_history(self, session_id: str = "default", limit: int = 5) -> List[QAResult]:
        """
        【辅助·读】LRANGE 历史列表；Redis 空则读父类内存。
        注意：不负责 cache_hit，与 try_get 分工不同。
        """
        rows = self._client.lrange(self._hist_key(session_id), 0, limit - 1)
        out: List[QAResult] = []
        for raw in rows:
            try:
                out.append(SessionCacheEntry.from_json(raw).to_qa_result())
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
        if out:
            return out
        return super().get_recent_history(session_id, limit=limit)

    def clear_session(self, session_id: str = "default") -> None:
        """删内存 + 删该 session 在 Redis 上的 hist / eidx / 全部 entry key。"""
        super().clear_session(session_id)
        idx = self._index_key(session_id)
        keys = list(self._client.smembers(idx))
        keys.append(self._hist_key(session_id))
        keys.append(idx)
        if keys:
            self._client.delete(*keys)
