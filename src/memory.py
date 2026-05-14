"""
memory.py — Session Memory（会话级轻量缓存）

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
- 在 `QAEngine.ask` **末尾**调用 `add_record`：一次问答结束后再写入，避免影响当次检索。
- 当前实现：`qa_history` 全量列表 + `query_to_pages` 映射「原问句 → 上次命中 page_id 列表」。
- 生产可换 `infra/redis_memory.RedisSessionMemory`（bootstrap 里按 `SETTINGS.session_backend` 切换）。

================================================================================
【类比 Android】
================================================================================
- 像 **ViewModel 里缓存上一次搜索结果的 `SavedStateHandle`**，或「会话级 Repository 内存一级缓存」。
- 不负责持久化用户账号，只降低**同会话重复问**的检索成本（演示向）。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `Dict[str, List[str]]`：Map 的 key 为 str，value 为 str 列表；Kotlin `Map<String, List<String>>`。
- `self.query_to_pages.get(query, [])`：`get` 带默认值，等价 `getOrDefault`。
- `def add_record(self, result: QAResult) -> None`：`-> None` 表示无返回值（过程型方法）。

会话级缓存（Session Memory）。

真实线上场景中，用户会追问同一份文档，重复检索会浪费时延与成本。
因此我们缓存：
1) 历史问答
2) 历史命中页面
"""

from dataclasses import dataclass, field
from typing import Dict, List

from .models import QAResult


@dataclass
class SessionMemory:
    """简单内存版缓存。生产中可替换成 Redis。"""

    qa_history: List[QAResult] = field(default_factory=list)
    query_to_pages: Dict[str, List[str]] = field(default_factory=dict)

    def add_record(self, result: QAResult) -> None:
        """记录一次问答结果。"""
        self.qa_history.append(result)
        self.query_to_pages[result.query] = [h.page_id for h in result.hits]

    def get_cached_pages(self, query: str) -> List[str]:
        """读取 query 对应的历史命中页。未命中时返回空列表。"""
        return self.query_to_pages.get(query, [])
