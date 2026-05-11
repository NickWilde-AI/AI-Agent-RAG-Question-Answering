"""
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
