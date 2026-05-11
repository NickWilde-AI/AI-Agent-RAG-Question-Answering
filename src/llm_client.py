"""真实 LLM/Embedding 客户端，统一封装 OpenAI 调用。"""

from __future__ import annotations

from dataclasses import dataclass
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
        api_key = SETTINGS.oapi_api_key if "oapi.uk" in SETTINGS.openai_base_url and SETTINGS.oapi_api_key else SETTINGS.openai_api_key
        if not api_key:
            return cls(client=None, chat_model=SETTINGS.openai_chat_model, embedding_model=SETTINGS.openai_embedding_model)
        kwargs = {"api_key": api_key}
        if SETTINGS.openai_base_url:
            kwargs["base_url"] = SETTINGS.openai_base_url
        return cls(client=OpenAI(**kwargs), chat_model=SETTINGS.openai_chat_model, embedding_model=SETTINGS.openai_embedding_model)

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def chat_text(self, system_prompt: str, user_prompt: str) -> str:
        if not self.client:
            raise RuntimeError("LLM client is not enabled.")
        try:
            resp = self.client.responses.create(
                model=self.chat_model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
            )
            return (resp.output_text or "").strip()
        except Exception:
            resp = self.client.chat.completions.create(
                model=self.chat_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
            )
            return (resp.choices[0].message.content or "").strip()

    def choose_tool(self, query: str, tools: List[Dict[str, Any]]) -> Optional[str]:
        """通过 OpenAI function calling 选择工具名。"""
        if not self.client:
            raise RuntimeError("LLM client is not enabled.")
        resp = self.client.chat.completions.create(
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
        tool_calls = resp.choices[0].message.tool_calls or []
        if not tool_calls:
            return None
        return tool_calls[0].function.name

    def embed(self, text: str) -> List[float]:
        if not self.client:
            raise RuntimeError("Embedding client is not enabled.")
        resp = self.client.embeddings.create(model=self.embedding_model, input=text)
        return list(resp.data[0].embedding)
