"""外部能力适配层：多模态 embedding / rerank / VLM / 图表解析。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib import request

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


