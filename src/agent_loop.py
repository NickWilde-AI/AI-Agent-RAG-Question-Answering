"""ReAct 思路的 Plan-Execute-Agent Loop（工程化简化版）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .config import SETTINGS
from .models import QAResult, RetrievalHit
from .pipeline import QAEngine


@dataclass
class LoopStep:
    """一次 loop 的执行轨迹。"""

    step_no: int
    plan: str
    branch: str
    verified: bool
    hit_count: int


@dataclass
class LoopRunResult:
    """Loop 运行结果。"""

    result: QAResult
    steps: List[LoopStep] = field(default_factory=list)


class PlanExecuteAgentLoop:
    """
    ReAct 思路（简化）：
    - Plan: 决定策略（top-k、分支）
    - Execute: 调用工具链
    - Observe/Verify: 校验证据
    - Retry: 不通过则扩召回重试
    """

    def __init__(self, engine: QAEngine, max_loops: int = 2) -> None:
        self.engine = engine
        self.max_loops = max_loops

    def run(self, query: str, topk: int = SETTINGS.topk_default) -> LoopRunResult:
        steps: List[LoopStep] = []
        current_topk = topk
        last_result: QAResult | None = None

        for step_no in range(1, self.max_loops + 1):
            last_result = self.engine.ask(query=query, topk=current_topk)
            steps.append(
                LoopStep(
                    step_no=step_no,
                    plan=f"retrieve_topk={current_topk}",
                    branch=last_result.branch,
                    verified=last_result.verified,
                    hit_count=len(last_result.hits),
                )
            )
            if last_result.verified:
                return LoopRunResult(result=last_result, steps=steps)
            current_topk = current_topk * SETTINGS.topk_retry_multiplier

        assert last_result is not None
        return LoopRunResult(result=last_result, steps=steps)

    @staticmethod
    def flatten_hits(result: QAResult) -> List[RetrievalHit]:
        """辅助方法：统一取最终命中页。"""
        return result.retry_hits or result.hits

