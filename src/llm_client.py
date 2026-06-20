"""
llm_client.py — 大模型与 Embedding 的**统一网关**（OpenAI 兼容 SDK）

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
- **Router**：`choose_tool` → function calling 选分支名。
- **Verifier / tools**：`chat_text` 做短文本判别或归纳（无 key 时上层不调用或抛错被捕获）。
- **Retriever**：`embed` 做文本向量（`enable_real_embedding` 时），否则检索侧用哈希 mock。
- `client is None`：表示未配置 API Key，全仓库走「规则 / 哈希」降级路径。

================================================================================
【类比 Android】
================================================================================
- 像一个 **Retrofit ApiService + OkHttpClient** 单例：统一 baseUrl、apiKey、超时与重试策略（此处重试在调用方）。
- `@classmethod def from_settings(cls) -> "LLMClient"`：类似 **静态工厂** `LLMClient.from(context)`，用全局配置构造实例。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `@classmethod`：第一个参数约定名 `cls`，指类本身；用于替代重载构造器的工厂方法。
- `-> "LLMClient"`：返回类型**字符串**形式 + `from __future__ import annotations`，避免类自引用时前向解析问题。
- `Optional[OpenAI]`：`OpenAI | None` 的同义；Kotlin `OpenAI?`。
- `resp.choices[0].message.content or ""`：`or` 对 `None`、空串提供默认值，类似 `?: ""`。
- `@property def enabled`：只读计算属性，用法 `if client.enabled`，类似 Kotlin `val enabled get()`。

真实 LLM/Embedding 客户端，统一封装 OpenAI 调用。
"""

from __future__ import annotations

from dataclasses import dataclass
import random
import time
from typing import Any, Dict, List, Optional

from openai import OpenAI

from .config import SETTINGS


@dataclass
class LLMClient:
    """OpenAI 客户端封装。无 key 时可被上层自动降级到 mock 路径。"""

    client: Optional[OpenAI]
    chat_model: str
    embedding_model: str

    @classmethod
    def from_settings(cls) -> "LLMClient":
        api_key = (
            SETTINGS.oapi_api_key
            if "oapi.uk" in SETTINGS.openai_base_url and SETTINGS.oapi_api_key
            else SETTINGS.openai_api_key
        )
        if not api_key:
            return cls(client=None, chat_model=SETTINGS.openai_chat_model, embedding_model=SETTINGS.openai_embedding_model)
        kwargs = {"api_key": api_key, "timeout": SETTINGS.external_api_timeout_seconds, "max_retries": 0}
        if SETTINGS.openai_base_url:
            kwargs["base_url"] = SETTINGS.openai_base_url
        return cls(client=OpenAI(**kwargs), chat_model=SETTINGS.openai_chat_model, embedding_model=SETTINGS.openai_embedding_model)

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def _call_with_retry(self, fn):
        last_exc: Optional[Exception] = None
        attempts = max(1, SETTINGS.llm_max_retries + 1)
        for idx in range(attempts):
            try:
                return fn()
            except Exception as exc:
                last_exc = exc
                if idx + 1 >= attempts:
                    break
                delay = min(
                    SETTINGS.llm_retry_base_seconds * (2 ** idx),
                    SETTINGS.llm_retry_max_seconds,
                )
                # 轻微抖动，避免并发重试雪崩
                delay = delay * (0.8 + random.random() * 0.4)
                time.sleep(delay)
        assert last_exc is not None
        raise last_exc

    def chat_text(self, system_prompt: str, user_prompt: str) -> str:
        if not self.client:
            raise RuntimeError("LLM client is not enabled.")
        try:
            resp = self._call_with_retry(
                lambda: self.client.responses.create(
                    model=self.chat_model,
                    input=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0,
                )
            )
            return (resp.output_text or "").strip()
        except Exception as exc:
            # 仅当兼容网关不支持 Responses API 时切 Chat Completions；认证、限流和服务端错误不应再打第二轮请求。
            if getattr(exc, "status_code", None) not in {400, 404, 405, 501}:
                raise
            resp = self._call_with_retry(
                lambda: self.client.chat.completions.create(
                    model=self.chat_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0,
                )
            )
            return (resp.choices[0].message.content or "").strip()

    def choose_tool(self, query: str, tools: List[Dict[str, Any]]) -> Optional[str]:
        """通过 OpenAI function calling 选择工具名。"""
        if not self.client:
            raise RuntimeError("LLM client is not enabled.")
        resp = self._call_with_retry(
            lambda: self.client.chat.completions.create(
                model=self.chat_model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是企业知识库 Agent Router，只能通过 function calling 选择一个最合适的工具。",
                    },
                    {"role": "user", "content": query},
                ],
                tools=tools,
                tool_choice="required",
                temperature=0,
            )
        )
        tool_calls = resp.choices[0].message.tool_calls or []
        if not tool_calls:
            return None
        return tool_calls[0].function.name

    def embed(self, text: str) -> List[float]:
        if not self.client:
            raise RuntimeError("Embedding client is not enabled.")
        resp = self._call_with_retry(lambda: self.client.embeddings.create(model=self.embedding_model, input=text))
        return list(resp.data[0].embedding)
