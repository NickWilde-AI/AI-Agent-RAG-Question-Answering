"""
Router Agent（规则版）。

真实项目中可以替换为 LLM function calling。
这里用规则来保证“离线可运行且稳定可复现”。
"""

from __future__ import annotations

from typing import Optional

from .config import SETTINGS
from .llm_client import LLMClient


class RouterAgent:
    """
    将 query 路由到四条分支：
    - fact_qa：单页事实抽取
    - multi_page_qa：跨页归纳推理
    - chart_qa：图表数值/趋势问题
    - translate_qa：跨语种翻译/抽取
    """

    BRANCH_FACT = "fact_qa"
    BRANCH_MULTI = "multi_page_qa"
    BRANCH_CHART = "chart_qa"
    BRANCH_TRANSLATE = "translate_qa"
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
        {
            "type": "function",
            "function": {
                "name": BRANCH_TRANSLATE,
                "description": "跨语种文档翻译、外文手册字段抽取或中英文含义解释。",
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
                if result in {self.BRANCH_FACT, self.BRANCH_MULTI, self.BRANCH_CHART, self.BRANCH_TRANSLATE}:
                    return result
            result = self.llm_client.chat_text(
                system_prompt=(
                    "你是一个路由器。仅返回一个分支名："
                    "fact_qa, multi_page_qa, chart_qa, translate_qa。"
                    "不要输出任何额外文本。"
                ),
                user_prompt=f"问题：{query}",
            ).strip()
            if result in {self.BRANCH_FACT, self.BRANCH_MULTI, self.BRANCH_CHART, self.BRANCH_TRANSLATE}:
                return result
        except Exception:
            return None
        return None

    def route(self, query: str) -> str:
        llm_branch = self._route_with_llm(query)
        if llm_branch:
            return llm_branch

        q = query.lower()
        # 翻译问题优先匹配
        if any(x in q for x in ["翻译", "中文含义", "英文", "日文", "外文"]):
            return self.BRANCH_TRANSLATE
        # 图表问题
        if any(x in q for x in ["图表", "柱状", "折线", "趋势", "数值", "销售额", "kpi"]):
            return self.BRANCH_CHART
        # 跨页推理问题
        if any(x in q for x in ["谁负责", "谁介绍", "跨页", "多页", "汇报", "ppt"]):
            return self.BRANCH_MULTI
        # 默认事实抽取
        return self.BRANCH_FACT
