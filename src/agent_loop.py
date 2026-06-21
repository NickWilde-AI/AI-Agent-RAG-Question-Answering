"""
agent_loop.py — 外层「Plan–Execute–Verify」循环（扩 top-k 重试）

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
- `config.SETTINGS.enable_plan_execute_loop == True` 且 `api.py` 走本类时生效。
- 每一轮调用 `QAEngine.ask`（**内部**已含：检索→路由→生成→校验→必要时扩 top-k 一次）。
- 若整轮结束 `verified` 仍为 False：把 `current_topk *= topk_retry_multiplier`，**再跑一轮** `engine.ask`（最多 `max_loops` 次）。
- 对应简历话术：**Observe（看 verified）→ Retry（加大检索范围）**；不是完整 LangGraph 状态机，而是工程化简化。

================================================================================
【类比 Android】
================================================================================
- 像 `while (!success && attempts < max)` 里反复调同一个 `Interactor.execute(params)`，
  每次只改 `params.pageSize`（这里等价于 top-k）。
- `LoopStep` ≈ 一条 **Breadcrumb / 埋点事件**，方便接口返回 `loop_steps` 给前端展示。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `for step_no in range(1, self.max_loops + 1)`：`range` 上界**开区间**；从 1 计数到 max_loops（含）。
- `last_result: QAResult | None`：Python 3.10+ **联合类型**，等价 `Optional[QAResult]`；Kotlin 即 `QAResult?`。
- `assert last_result is not None`：静态分析/人读保证循环后非空；生产代码更常用显式 `if` 抛业务异常。
- `@staticmethod`：不需要 `self` 的工具函数挂在类命名空间下，类似 Java `static void util()`。

ReAct 思路的 Plan-Execute-Agent Loop（工程化简化版）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .config import SETTINGS
from .models import QAResult, RetrievalHit
from .pipeline import QAEngine


@dataclass
class LoopStep:
    """
    白话：外层循环「第几轮」留下的一行脚印，给前端 / 日志看。

    每一轮只记：第几步、当时打算检索几页、走哪条题型、校验过没过、命中了几条。
    """

    step_no: int
    plan: str
    branch: str
    verified: bool
    hit_count: int


@dataclass
class LoopRunResult:
    """
    白话：外层循环跑完后的「最后一版答案」+「每一轮脚印列表」。
    """

    result: QAResult
    steps: List[LoopStep] = field(default_factory=list)


class PlanExecuteAgentLoop:
    """
    -------------------------------------------------------------------------
    白话：PlanExecuteAgentLoop 是干嘛的？
    -------------------------------------------------------------------------
    你可以把它想成「**套在外面的一圈 for 循环**」，专门处理这种情况：

    「**一整遍**问答案流程已经跑完了（里面该检索、该答题、该验真假、该扩一次检索，
    全都做过了），但最后验真假还是没过。」

    这时候 **不立刻放弃**，而是：
    - **把「一次多拿几页材料」这个数字再放大**（例如 3 页变 6 页、再变 12 页）；
    - **再从头跑一整遍问答案**（里面又会重新检索、重新答题、重新验真假……）；
    - 最多跑 `max_loops` 轮（默认 2 轮），有一轮验过了就提前收工。

    和「里面那一套」的关系：
    - **里面那一套**（引擎里那一次问答）：已经包含「验不过就多捞一页材料再答一遍」这种**小范围**补救。
    - **外面这一圈**（本类）：是「**整遍**仍不行，就换更大的捞页数量，**再整遍来**」这种**大范围**补救。

    默认配置里这圈常常是**关掉的**，因为多跑整遍会慢、会贵；打开后接口里会多一个「每一轮脚印」列表给前端展示。

    简历里的 ReAct / Plan-and-Execute：这里用工程话翻译就是——
    「看一眼验过没有 → 没过就改计划（多拿页）→ 再执行一整遍」。
    """

    def __init__(self, engine: QAEngine, max_loops: int = 2) -> None:
        # 白话：手里握着「那一整套问答案的机器」；max_loops = 最多少整遍重来
        self.engine = engine
        self.max_loops = max_loops

    def run(self, query: str, topk: int = SETTINGS.topk_default, session_id: str = "default", event_callback: Optional[Callable] = None) -> LoopRunResult:
        # 白话：脚印空列表；current_topk = 这一轮打算「最多捞几页材料」
        steps: List[LoopStep] = []
        current_topk = topk
        last_result: QAResult | None = None

        for step_no in range(1, self.max_loops + 1):
            # 白话：用当前的「捞页数量」跑**完整一整遍**问答案（内部该验的都会验）
            last_result = self.engine.ask(query=query, topk=current_topk, session_id=session_id,event_callback=event_callback)
            # 白话：记一行脚印，方便你知道这一轮用了多少页、验过没
            steps.append(
                LoopStep(
                    step_no=step_no,
                    plan=f"retrieve_topk={current_topk}",
                    branch=last_result.branch,
                    verified=last_result.verified,
                    hit_count=len(last_result.hits),
                )
            )
            # 白话：验过了就收工，把最后一版结果和脚印一起返回
            if last_result.verified:
                return LoopRunResult(result=last_result, steps=steps)
            # 白话：没验过 → 下一轮多捞页（乘倍数）；然后 for 继续下一轮整遍 ask
            current_topk = current_topk * SETTINGS.topk_retry_multiplier

        assert last_result is not None
        # 白话：轮数用完了还没验过，也返回最后一版（总比没有强），脚印里能看到哪几轮没过
        return LoopRunResult(result=last_result, steps=steps)

    @staticmethod
    def flatten_hits(result: QAResult) -> List[RetrievalHit]:
        """
        白话：对外展示「最终到底用了哪些命中页」时，若有「第二次捞页」的结果就优先用那次，否则用第一次。
        """
        return result.retry_hits or result.hits
