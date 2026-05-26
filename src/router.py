"""
router.py — L1 路由：把自然语言问题分到四条工具分支

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
- 在 `pipeline.QAEngine.ask` 里 **检索之后、调用 tools 之前** 调用 `route(query)`。
- 输出 `branch`：`fact_qa` / `multi_page_qa` / `chart_qa`。
- 优先级：若配置允许且 LLM 可用 → `_route_with_llm`（含 OpenAI **function calling** 选工具名）；否则走**关键词规则**兜底，保证离线可复现。

================================================================================
【类比 Android】
================================================================================
- 像 `Intent` 解析：`ACTION_VIEW` vs `ACTION_SEND` 决定走哪个 Activity；这里是字符串分支名决定走哪条工具链。
- `ROUTER_TOOLS` JSON schema ≈ 给 LLM 看的「manifest 里声明的 exported service 列表」，模型只能回调其中一个。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `if llm_branch:`：Python 对 `None`、空串走 false；Kotlin 里注意不要用 `if (x)` 对可空 String 混用 trim 习惯。
- `{self.BRANCH_FACT, ...}`：字面量 **set**，`in` 成员测试 O(1) 均摊，类似 `EnumSet` / `HashSet.contains`。
- `any(x in q for x in [...])`：`any` + 生成器表达式，短路求值；有一个 True 即 True。
- `except Exception: return None`：吞掉 LLM 异常后回退规则路由；线上通常会打日志。

Router Agent（规则版 + 可选 LLM）。

真实项目中可以替换为纯 LLM function calling。
这里用规则来保证「离线可运行且稳定可复现」。
"""

from __future__ import annotations

from typing import Optional

from prometheus_client import Counter
import sentry_sdk

from .config import SETTINGS
from .llm_client import LLMClient
from .resilience import CircuitBreaker

_ROUTER_CB = CircuitBreaker(
    failure_threshold=SETTINGS.router_cb_failures,
    recovery_seconds=SETTINGS.router_cb_recovery_seconds,
)
ROUTER_RULE_FALLBACK = Counter(
    "rag_router_rule_fallback_total",
    "Router fell back to rule routing (LLM unavailable or circuit open)",
)


def router_circuit_open() -> bool:
    return SETTINGS.enable_router_circuit_breaker and not _ROUTER_CB.allow()


class RouterAgent:
    """将 query 路由到三条分支：fact / multi_page / chart。"""

    BRANCH_FACT = "fact_qa"
    BRANCH_MULTI = "multi_page_qa"
    BRANCH_CHART = "chart_qa"
    PRIMARY_BRANCHES = {BRANCH_FACT, BRANCH_MULTI, BRANCH_CHART}
    ROUTER_TOOLS = [
        {
            "type": "function",
            "function": {
                "name": BRANCH_FACT,
                "description": "单页事实抽取，适合字段、定义、单据号、负责人等单一页面问题。",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": BRANCH_MULTI,
                "description": "跨页 PPT 或多页文档归纳，适合多人、多阶段、跨页对比问题。",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": BRANCH_CHART,
                "description": "图表、趋势、KPI、数值读取或数值校验问题。",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
    ]

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self.llm_client = llm_client

    def _route_with_llm(self, query: str) -> Optional[str]:
        if not (SETTINGS.enable_llm_router and self.llm_client and self.llm_client.enabled):
            return None
        try:
            if SETTINGS.enable_function_calling_router:
                result = self.llm_client.choose_tool(query=query, tools=self.ROUTER_TOOLS)
                if result in {self.BRANCH_FACT, self.BRANCH_MULTI, self.BRANCH_CHART}:
                    return result
            result = self.llm_client.chat_text(
                system_prompt=(
                    "你是一个路由器。仅返回一个分支名："
                    "fact_qa, multi_page_qa, chart_qa。"
                    "不要输出任何额外文本。"
                ),
                user_prompt=f"问题：{query}",
            ).strip()
            if result in {self.BRANCH_FACT, self.BRANCH_MULTI, self.BRANCH_CHART}:
                return result
        except Exception as exc:
            if SETTINGS.sentry_dsn:
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag("component", "router")
                    scope.set_tag("phase", "llm_route")
                    sentry_sdk.capture_exception(exc)
            return None
        return None

    def route(self, query: str) -> str:
        use_llm = (
            SETTINGS.enable_llm_router
            and self.llm_client
            and self.llm_client.enabled
            and (not SETTINGS.enable_router_circuit_breaker or _ROUTER_CB.allow())
        )
        llm_routed = False
        if use_llm:
            try:
                llm_branch = self._route_with_llm(query)
                if llm_branch:
                    _ROUTER_CB.record_success()
                    llm_routed = True
                    return llm_branch
                _ROUTER_CB.record_failure()
            except Exception:
                _ROUTER_CB.record_failure()
        # 只要开启了 LLM Router 但最终未走 LLM 分支，就记一次规则兜底。
        if SETTINGS.enable_llm_router and not llm_routed:
            ROUTER_RULE_FALLBACK.inc()

        q = query.lower()
        if any(x in q for x in ["图表", "柱状", "折线", "趋势", "数值", "销售额", "kpi"]):
            return self.BRANCH_CHART
        # 跨页推理问题
        if any(x in q for x in ["谁负责", "谁介绍", "跨页", "多页", "汇报", "ppt"]):
            return self.BRANCH_MULTI
        # 默认事实抽取
        return self.BRANCH_FACT

    def fallback_branch(self, branch: str) -> str:
        """
        Verifier 失败时的保守回退：
        - 图表题经常是检索噪声导致读错，先回退到事实抽取
        - 多页题在证据不足时也回退到事实抽取
        - 事实题可尝试多页聚合
        """
        if branch == self.BRANCH_CHART:
            return self.BRANCH_FACT
        if branch == self.BRANCH_MULTI:
            return self.BRANCH_FACT
        return self.BRANCH_FACT
