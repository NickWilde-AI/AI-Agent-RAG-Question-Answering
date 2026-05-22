"""
models.py — 领域数据模型（Page / 检索命中 / 一次问答结果）

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
定义流水线里「传来传去的 DTO」：
- `Page`：知识库一页（文本、结构化字段、可选图片路径等）
- `RetrievalHit`：检索返回的 page_id + score
- `QAResult`：一次 ask 的完整轨迹（给 API JSON、日志、Session 用）

================================================================================
【类比 Android / Java】
================================================================================
- `Page` ≈ Room `@Entity` 或网络 DTO：描述一页材料的字段集合。
- `RetrievalHit` ≈ 搜索结果里的一条 `(id, relevanceScore)`。
- `QAResult` ≈ 一次 UseCase 的 `Result` 封装：把 query、分支、答案、是否 verified、hits 打包返回 UI/接口。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `@dataclass`：自动生成构造函数等；字段顺序即构造参数顺序（类似 data class 主构造器）。
- `field(default_factory=dict)`：默认值不能写 `chart_data={}`（可变默认会坑所有实例共享同一个 dict）；
  用 `default_factory=dict` 等价于「每次 new 一个新 HashMap」。
- `Optional[str] = None`：类型可能是 str 或 None；Kotlin 里像 `String?`。
- `List[RetrievalHit]`：`typing` 泛型标注（Python 3.9+ 也可写 `list[RetrievalHit]`，本仓库混用两种风格）。

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
class StageTrace:
    """单个阶段的执行轨迹。"""

    stage: str
    elapsed_ms: int
    detail: Dict[str, str] = field(default_factory=dict)


@dataclass
class AgentTrace:
    """一次问答的全链路轨迹。"""

    route_branch: str
    fallback_triggered: bool
    retry_reason: str
    stages: List[StageTrace] = field(default_factory=list)


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
    trace: Optional[AgentTrace] = None
