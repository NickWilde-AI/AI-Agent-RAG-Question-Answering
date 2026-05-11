"""
核心数据结构定义。

真实项目里，这些对象通常会对应：
- 向量库中的页面记录
- 检索返回的候选页
- Agent 每次问答的运行轨迹（便于回放与评估）
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Page:
    """
    知识库中的“页面级”文档单元。

    为什么按页：
    - 页面是视觉语义天然单位（布局、图表、标题、表格都在同一页）
    - 与传统“段落切块”相比，更适配图文混排文档
    """

    page_id: str
    doc_id: str
    doc_type: str
    language: str
    content: str
    chart_data: Dict[str, float] = field(default_factory=dict)
    fields: Dict[str, str] = field(default_factory=dict)
    people: List[str] = field(default_factory=list)
    image_path: Optional[str] = None
    page_no: Optional[int] = None
    source_file: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class RetrievalHit:
    """单条检索命中结果。"""

    page_id: str
    score: float


@dataclass
class QAResult:
    """
    一次问答的完整结果。
    面试中可以强调：保留中间状态，便于 debug、回放、A/B 对比与持续评测。
    """

    query: str
    rewritten_query: str
    branch: str
    answer: str
    verified: bool
    hits: List[RetrievalHit]
    retry_hits: Optional[List[RetrievalHit]] = None
    tool_scores: Dict[str, float] = field(default_factory=dict)
    source_files: List[str] = field(default_factory=list)
