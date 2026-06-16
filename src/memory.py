from __future__ import annotations

"""
Session Memory — 会话级记忆（本文件为默认「进程内」实现）。

调用链（主逻辑不在这里定义，在这里实现）：
  pipeline.QAEngine.ask 开头 → try_get()     重复问句则 cache_hit，跳过检索/LLM
  pipeline.QAEngine.ask 末尾 → add_record()  每次问答结束后写入

两类数据（勿混）：
  _entries[session_id][norm_query]  问句 → 上次完整答案（用于短路，≈ Map<关键词, 搜索结果>）
  _history[session_id]            按时间追加的 QA 列表（≈ 搜索历史 RecyclerView 数据源）

Android 类比：
  try_get / add_record  ≈ 发起搜索前查缓存、搜索成功后写入缓存
  get_recent_history    ≈ 读「最近搜索记录」列表，不负责短路（Router 追问扩展用）

配置：bootstrap 注入；RAG_SESSION_BACKEND=redis 时用 infra/redis_memory.py。
"""

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from .models import QAResult, RetrievalHit


def normalize_query(query: str) -> str:
    """缓存 key：去首尾空白、小写、合并连续空格。不做语义相似匹配。"""
    q = (query or "").strip().lower()
    return re.sub(r"\s+", " ", q)


@dataclass
class SessionCacheEntry:
    """一条可 JSON 序列化的问答缓存（写入 Redis 时也用同一结构）。"""

    query: str  # 用户原问句（展示用）
    norm_query: str  # normalize_query(query)，与 _entries 的 key 一致
    rewritten_query: str
    branch: str  # 写入时的分支；读出时 to_qa_result 会改为 cache_hit
    answer: str
    verified: bool
    page_ids: List[str]  # 上次检索命中的 page_id
    source_files: List[str] = field(default_factory=list)

    def to_qa_result(self) -> QAResult:
        """读缓存：包装为 QAResult，branch 固定 cache_hit 便于日志/指标区分。"""
        return QAResult(
            query=self.query,
            rewritten_query=self.rewritten_query,
            branch="cache_hit",
            answer=self.answer,
            verified=self.verified,
            hits=[RetrievalHit(page_id=p, score=1.0) for p in self.page_ids],
            source_files=list(self.source_files),
        )

    @classmethod
    def from_qa_result(cls, result: QAResult, norm_query: str) -> SessionCacheEntry:
        """写缓存：从一次真实 ask 的结果拷贝字段。"""
        return cls(
            query=result.query,
            norm_query=norm_query,
            rewritten_query=result.rewritten_query,
            branch=result.branch,
            answer=result.answer,
            verified=result.verified,
            page_ids=[h.page_id for h in result.hits],
            source_files=list(result.source_files),
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> SessionCacheEntry:
        data = json.loads(raw)
        return cls(**data)


@dataclass
class SessionMemory:
    """
    进程内 Session Memory（单机 demo / Redis 不可用时的回退）。

    对外只需关心：try_get、add_record、get_recent_history。
    """

    max_history: int = 50  # 每个 session_id 最多保留多少条历史
    cache_verified_only: bool = True  # True 时仅 verified 的结果写入 _entries

    # session_id → (norm_query → 缓存条目)
    _entries: Dict[str, Dict[str, SessionCacheEntry]] = field(
        default_factory=lambda: defaultdict(dict), init=False, repr=False
    )
    # session_id → 按时间追加的 QAResult 列表
    _history: Dict[str, List[QAResult]] = field(
        default_factory=lambda: defaultdict[str, List[QAResult]](list), init=False, repr=False
    )

    def try_get(self, session_id: str, query: str) -> Optional[QAResult]:
        """
        【主链路·读】pipeline.ask 在检索前调用。

        命中：返回 cache_hit 的 QAResult。
        未命中：返回 None，继续走 retrieve → 生成 → 校验。
        """
        norm = normalize_query(query)
        entry = self._entries.get(session_id, {}).get(norm)
        if entry is None:
            return None
        return entry.to_qa_result()

    def add_record(self, result: QAResult, session_id: str = "default") -> None:
        """
        【主链路·写】pipeline.ask 每次 return 前调用（含闲聊/拒答路径）。

        1) 始终追加 _history（便于 get_recent_history）
        2) 仅当满足条件时更新 _entries（问句级可复用缓存）：
           - 非 cache_hit（避免缓存套缓存）
           - cache_verified_only 时要求 result.verified
        """
        hist = self._history[session_id]
        hist.append(result)
        if len(hist) > self.max_history:
            self._history[session_id] = hist[-self.max_history :]

        if result.branch == "cache_hit":
            return
        if self.cache_verified_only and not result.verified:
            return

        norm = normalize_query(result.query)
        self._entries[session_id][norm] = SessionCacheEntry.from_qa_result(result, norm)

    def get_cached_pages(self, query: str, session_id: str = "default") -> List[str]:
        """
        【扩展·读】只取上次命中的 page_id 列表。
        主链路用 try_get 返回整包答案；若将来「只跳过检索、仍重新生成」可用本方法。
        """
        norm = normalize_query(query)
        entry = self._entries.get(session_id, {}).get(norm)
        return list(entry.page_ids) if entry else []

    def get_recent_history(self, session_id: str = "default", limit: int = 5) -> List[QAResult]:
        """
        【辅助·读】最近 N 条问答时间线。不负责 cache_hit 短路。
        用途：多轮对话 UI、get_context_snippet、后续 Router 注入历史。
        """
        return self._history.get(session_id, [])[-limit:]

    def get_context_snippet(self, session_id: str = "default", limit: int = 3) -> str:
        """把最近几条 QA 拼成短文本，可塞进 LLM / Router 的 system prompt。"""
        lines: List[str] = []
        for r in self.get_recent_history(session_id, limit=limit):
            lines.append(f"Q: {r.query}\nA: {r.answer[:200]}")
        return "\n---\n".join(lines)

    def clear_session(self, session_id: str = "default") -> None:
        """清空某会话的缓存与历史（文档更新、用户登出等场景）。"""
        self._entries.pop(session_id, None)
        self._history.pop(session_id, None)
