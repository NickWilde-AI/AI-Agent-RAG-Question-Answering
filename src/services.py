"""
services.py — 外部能力适配层（HTTP JSON）：多模态 embedding / rerank / VLM / 图表 / 翻译

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
- **检索侧**：`MultimodalEmbeddingClient`、`ColPaliRerankClient` 被 `retriever` 使用。
- **生成侧**：`VLMClient`、`ChartParsingClient`、`TranslationEngineClient` 被 `tools` / `verifier` 使用。
- 未配置 URL 或开关关闭时：各 client 的 `enabled` 为 False，上层静默走本地逻辑。

================================================================================
【类比 Android】
================================================================================
- 每个 `@dataclass` Client ≈ **Retrofit Service 接口 + 一个极简 RemoteDataSource 实现**；`post_json` 是用标准库手搓 POST（减少依赖）。
- `urllib.request`：类似 `HttpURLConnection`，demo 够用；生产常换 `OkHttp` 级特性（连接池、拦截器）。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `@dataclass` 在只有字段的类上：自动生成 `__init__`，字段默认值直接写在类体（类似 Kotlin `data class` 主构造器默认值）。
- `payload: Dict[str, Any]`：`Any` ≈ JSON 动态结构在类型上的「顶」。
- `json.dumps(..., ensure_ascii=False).encode("utf-8")`：网络字节必须 UTF-8；`ensure_ascii=False` 保留中文可读。
- `with request.urlopen(...) as resp:`：`urlopen` 返回 context manager，读完自动关流。

企业级外部能力适配层。

这里统一封装多模态 embedding、VLM、图表解析和翻译服务。没有配置外部服务时，
上层会自动走本地规则路径，保证工程仍可运行。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib import parse, request

from .config import SETTINGS


def post_json(url: str, payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
    """用标准库发 JSON POST，避免额外依赖。"""
    if not url:
        raise RuntimeError("External service url is empty.")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout or SETTINGS.external_api_timeout_seconds) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


@dataclass
class MultimodalEmbeddingClient:
    """ColPali / MiniCPM-V 等页图 embedding 服务适配。"""

    api_url: str = SETTINGS.multimodal_embedding_api

    @property
    def enabled(self) -> bool:
        return bool(SETTINGS.enable_multimodal_embedding and self.api_url)

    def embed_text(self, text: str) -> List[float]:
        data = post_json(self.api_url, {"text": text})
        return [float(x) for x in data["embedding"]]

    def embed_image(self, image_path: str, text_hint: str = "") -> List[float]:
        data = post_json(self.api_url, {"image_path": image_path, "text_hint": text_hint})
        return [float(x) for x in data["embedding"]]


@dataclass
class ColPaliRerankClient:
    """ColPali late-interaction rerank 服务适配。"""

    api_url: str = SETTINGS.colpali_rerank_api

    @property
    def enabled(self) -> bool:
        return bool(SETTINGS.enable_colpali_rerank and self.api_url)

    def rerank(self, query: str, page_ids: List[str]) -> Dict[str, float]:
        data = post_json(self.api_url, {"query": query, "page_ids": page_ids})
        scores = data.get("scores", {})
        return {str(k): float(v) for k, v in scores.items()}

    def rerank_pages(self, query: str, pages: List[Dict[str, str]]) -> Dict[str, float]:
        """对带图片路径的候选页做 ColPali late-interaction 重排。"""
        if not pages:
            return {}
        max_n = SETTINGS.colpali_rerank_max_pages
        pages = pages[:max_n] if max_n > 0 else pages
        data = post_json(
            self.api_url,
            {"query": query, "pages": pages},
            timeout=SETTINGS.colpali_rerank_timeout_seconds,
        )
        scores = data.get("scores", {})
        return {str(k): float(v) for k, v in scores.items()}


@dataclass
class VLMClient:
    """单图、多图 VLM 推理和多模态 verifier 统一适配。"""

    api_url: str = SETTINGS.vlm_api

    @property
    def enabled(self) -> bool:
        return bool(self.api_url)

    def answer(self, query: str, image_paths: List[str], mode: str) -> str:
        data = post_json(self.api_url, {"query": query, "image_paths": image_paths, "mode": mode})
        return str(data.get("answer", "")).strip()

    def verify(self, query: str, answer: str, image_paths: List[str]) -> Optional[bool]:
        data = post_json(self.api_url, {"query": query, "answer": answer, "image_paths": image_paths, "mode": "verify"})
        value = data.get("verified")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"yes", "true", "1", "pass"}
        return None


@dataclass
class ChartParsingClient:
    """图表解析服务适配，用于 chart_qa 分支的数值读取和校验。"""

    api_url: str = SETTINGS.chart_parsing_api

    @property
    def enabled(self) -> bool:
        return bool(self.api_url)

    def parse(self, query: str, image_paths: List[str]) -> Dict[str, float]:
        data = post_json(self.api_url, {"query": query, "image_paths": image_paths})
        chart_data = data.get("chart_data", {})
        return {str(k): float(v) for k, v in chart_data.items()}


@dataclass
class TranslationEngineClient:
    """Google / DeepL / GPT 翻译引擎适配。"""

    def google(self, text: str) -> Optional[str]:
        if not (SETTINGS.google_translate_api and SETTINGS.google_translate_api_key):
            return None
        data = post_json(
            SETTINGS.google_translate_api,
            {"q": text, "target": "zh-CN", "key": SETTINGS.google_translate_api_key},
        )
        return str(data.get("translatedText") or data.get("translation") or "").strip() or None

    def deepl(self, text: str) -> Optional[str]:
        if not SETTINGS.deepl_api_key:
            return None
        body = parse.urlencode({"auth_key": SETTINGS.deepl_api_key, "text": text, "target_lang": "ZH"}).encode("utf-8")
        req = request.Request(SETTINGS.deepl_api, data=body, method="POST")
        with request.urlopen(req, timeout=SETTINGS.external_api_timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        translations = data.get("translations") or []
        if not translations:
            return None
        return str(translations[0].get("text", "")).strip() or None

    def oapi_chat(self, text: str) -> Optional[str]:
        """通过 OpenAI 兼容 Chat Completions 网关做翻译。"""
        if not SETTINGS.oapi_api_key:
            return None
        payload = {
            "model": SETTINGS.oapi_translation_model,
            "messages": [
                {"role": "system", "content": "你是企业文档翻译助手。请输出准确、自然、术语一致的中文。"},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            SETTINGS.oapi_chat_completions_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {SETTINGS.oapi_api_key}",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=SETTINGS.external_api_timeout_seconds) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        choices = result.get("choices") or []
        if not choices:
            return None
        return str(choices[0].get("message", {}).get("content", "")).strip() or None
