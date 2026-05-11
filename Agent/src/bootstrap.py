"""依赖组装模块：把所有组件在一个地方初始化。"""

from __future__ import annotations

from .agent_loop import PlanExecuteAgentLoop
from .config import SETTINGS
from .infra.redis_memory import RedisSessionMemory
from .llm_client import LLMClient
from .memory import SessionMemory
from .pipeline import QAEngine
from .retriever import PageRetriever
from .router import RouterAgent
from .verifier import Verifier


def build_engine(data_path: str = "data/demo_pages.json") -> QAEngine:
    """
    构建 QAEngine。

    为什么单独做 bootstrap：
    - main.py、API 服务都要用同一套初始化逻辑
    - 面试时可强调“依赖组装集中管理”，避免散落在多个入口文件
    """
    llm_client = LLMClient.from_settings()
    retriever = PageRetriever.from_json(data_path, llm_client=llm_client)
    memory: SessionMemory
    if SETTINGS.session_backend == "redis":
        try:
            memory = RedisSessionMemory(redis_url=SETTINGS.redis_url, ttl_seconds=SETTINGS.redis_ttl_seconds)
        except Exception:
            # 环境未准备好时自动回退，保证 demo 可运行。
            memory = SessionMemory()
    else:
        memory = SessionMemory()

    return QAEngine(
        retriever=retriever,
        router=RouterAgent(llm_client=llm_client),
        memory=memory,
        verifier=Verifier(llm_client=llm_client),
    )


def build_agent_loop(data_path: str = "data/demo_pages.json") -> PlanExecuteAgentLoop:
    """构建 Plan-Execute-Agent Loop。"""
    engine = build_engine(data_path=data_path)
    return PlanExecuteAgentLoop(engine=engine, max_loops=2)
