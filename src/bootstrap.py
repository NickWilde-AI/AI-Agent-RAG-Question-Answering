"""
bootstrap.py — 依赖组装（Dependency Injection / 工厂方法）

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
把 `LLMClient`、`PageRetriever`、`RouterAgent`、`Verifier`、`SessionMemory` 拼成 `QAEngine`；
再可选包一层 `PlanExecuteAgentLoop`。相当于 Spring `@Configuration` + `@Bean` 方法集中写在一处，
避免 `api.py` 和 `main.py` 各复制一套 new 代码。

================================================================================
【类比 Android】
================================================================================
- 像自定义 `Application` 里初始化单例，或 Hilt `@Module` / `@Provides`：只负责「谁依赖谁」，不写业务分支。
- `build_engine` / `build_agent_loop`：类似 `ViewModelFactory` 或 `ComponentFactory`。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `from __future__ import annotations`：允许类型注解里写尚未定义的类名（前向引用），解析推迟，减少循环 import 问题。
- `memory: SessionMemory` 后接 `if/else` 赋值：Python 无「声明类型再赋值」关键字，类型注解仅给类型检查器看。
- `except Exception:` 后 `memory = SessionMemory()`：演示环境 Redis 不可用时**降级**（resilience 模式），面试可强调「可运行优先」。

依赖组装模块：把所有组件在一个地方初始化。
"""

from __future__ import annotations

from typing import Any

from .agent_loop import PlanExecuteAgentLoop
from .config import SETTINGS
from .infra.redis_memory import RedisSessionMemory
from .langgraph_engine import LangGraphQAEngine
from .llm_client import LLMClient
from .memory import SessionMemory
from .pipeline import QAEngine
from .retriever import PageRetriever
from .router import RouterAgent
from .verifier import Verifier


def build_engine(data_path: str = "data/demo_pages.json") -> Any:
    """
    构建 QAEngine。

    为什么单独做 bootstrap：
    - main.py、API 服务都要用同一套初始化逻辑
    - 面试时可强调“依赖组装集中管理”，避免散落在多个入口文件
    """
    llm_client = LLMClient.from_settings()
    retriever = PageRetriever.from_json(data_path, llm_client=llm_client)
    mem_kw = {
        "max_history": SETTINGS.session_max_history,
        "cache_verified_only": SETTINGS.session_cache_require_verified,
    }
    memory: SessionMemory
    if SETTINGS.session_backend == "redis":
        try:
            memory = RedisSessionMemory(
                redis_url=SETTINGS.redis_url,
                ttl_seconds=SETTINGS.redis_ttl_seconds,
                **mem_kw,
            )
        except Exception:
            memory = SessionMemory(**mem_kw)
    else:
        memory = SessionMemory(**mem_kw)

    router = RouterAgent(llm_client=llm_client)
    verifier = Verifier(llm_client=llm_client)
    if SETTINGS.enable_langgraph:
        return LangGraphQAEngine(
            retriever=retriever,
            router=router,
            memory=memory,
            verifier=verifier,
        )
    return QAEngine(
        retriever=retriever,
        router=router,
        memory=memory,
        verifier=verifier,
    )


def build_agent_loop(data_path: str = "data/demo_pages.json") -> PlanExecuteAgentLoop:
    """构建 Plan-Execute-Agent Loop。"""
    engine = build_engine(data_path=data_path)
    return PlanExecuteAgentLoop(engine=engine, max_loops=2)
