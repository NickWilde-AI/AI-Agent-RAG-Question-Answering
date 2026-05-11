"""
Verifier：答案可证性校验。

真实系统常用多模态 LLM 判断：
“这个答案是否能在检索到的页面中被证据支持？”
"""

from __future__ import annotations

import re
from typing import List, Optional

from .config import SETTINGS
from .llm_client import LLMClient
from .models import Page
from .services import VLMClient


def _evidence_chunks_from_answer(answer: str) -> List[str]:
    """从答案中抽取可与页面正文做子串匹配的片段（兼容中文无空格分词）。"""
    at = answer
    for noise in (
        "[engine=gpt4o]",
        "[engine=google]",
        "[engine=deepl]",
        "[engine=llm]",
        "yes",
        "no",
    ):
        at = at.replace(noise, "")
    at = at.replace("依据文档：", "").strip()
    chunks = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z][a-zA-Z0-9_-]{2,}|\d{4,}", at)
    if chunks:
        return chunks[:48]
    compact = "".join(at.split())
    if len(compact) >= 4:
        return [compact[i : i + 4] for i in range(0, min(len(compact), 48), 2)]
    return []


class Verifier:
    """
    规则版 verifier。

    逻辑非常直观：如果答案关键词能在候选页文本中找到，就判定通过。
    """

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self.llm_client = llm_client

    def _verify_with_llm(self, answer: str, pages: List[Page]) -> Optional[bool]:
        if not (SETTINGS.enable_llm_verifier and self.llm_client and self.llm_client.enabled):
            return None
        evidence = "\n\n".join([f"[{p.page_id}] {p.content}" for p in pages[:5]])
        try:
            result = self.llm_client.chat_text(
                system_prompt=(
                    "你是可证性校验器。判断答案是否可被证据支持。"
                    "仅输出 YES 或 NO。"
                ),
                user_prompt=f"答案：{answer}\n证据：\n{evidence}",
            ).strip().upper()
            if result.startswith("YES"):
                return True
            if result.startswith("NO"):
                return False
        except Exception:
            return None
        return None

    def verify(self, answer: str, pages: List[Page]) -> bool:
        image_paths = [p.image_path for p in pages if p.image_path]
        vlm = VLMClient()
        if image_paths and vlm.enabled:
            try:
                judgement = vlm.verify(query="", answer=answer, image_paths=image_paths[:5])
                if judgement is not None:
                    return judgement
            except Exception:
                pass

        llm_judgement = self._verify_with_llm(answer, pages)
        if llm_judgement is not None:
            return llm_judgement

        all_text = " ".join((p.content or "") + " " + " ".join(p.fields.keys()) for p in pages).lower()
        answer_lower = answer.lower()
        chunks = _evidence_chunks_from_answer(answer_lower)
        if chunks:
            return any(c.lower() in all_text for c in chunks if len(c) >= 2)
        # 西文空格分词兜底
        answer_text = (
            answer_lower.replace("[engine=gpt4o]", "")
            .replace("[engine=google]", "")
            .replace("[engine=deepl]", "")
            .replace("[engine=llm]", "")
            .strip()
        )
        tokens = [t for t in answer_text.replace("（", " ").replace("）", " ").replace(":", " ").split() if len(t) >= 2]
        if tokens:
            return any(token in all_text for token in tokens)
        return len(answer.strip()) >= 8
