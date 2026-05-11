"""
教学版：OCR TextRAG 全流程类（单文件可读）。

这个类不是为了生产性能，而是为了让你看懂一条完整链路：
1) OCR 结果入库（用传入文本模拟）
2) 文本切块
3) 建立轻量“向量索引”（哈希向量）
4) 问题检索 top-k
5) 根据证据拼装答案
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Dict, List, Sequence, Tuple


@dataclass
class OcrPage:
    """一页 OCR 后的数据。"""

    doc_id: str
    page_no: int
    text: str


@dataclass
class TextChunk:
    """文本切块结果，包含来源元信息。"""

    chunk_id: str
    doc_id: str
    page_no: int
    text: str
    vector: List[float] = field(default_factory=list)


class OcrTextRagPipeline:
    """
    OCR TextRAG 教学类。

    你可以把它当作“传统 OCR 检索架构”的最小化代码模板。
    """

    def __init__(self, vector_dim: int = 64, chunk_size: int = 120, overlap: int = 30) -> None:
        self.vector_dim = vector_dim
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.pages: List[OcrPage] = []
        self.chunks: List[TextChunk] = []

    # ---------- 阶段 1：数据入库 ----------
    def ingest_ocr_pages(self, pages: Sequence[OcrPage]) -> None:
        """
        导入 OCR 结果并建立索引。

        生产系统对应步骤：
        - PDF/PPT 渲染
        - OCR 识别
        - 文本切块
        - embedding 入向量库
        """
        self.pages = list(pages)
        self.chunks = self._build_chunks(self.pages)
        for chunk in self.chunks:
            chunk.vector = self._embed(chunk.text)

    # ---------- 阶段 2：在线问答 ----------
    def ask(self, query: str, topk: int = 3) -> Dict[str, object]:
        """
        对外主方法：给问题，返回答案与证据。
        """
        if not self.chunks:
            return {"answer": "知识库为空，请先 ingest_ocr_pages。", "evidence": []}

        hits = self.retrieve(query=query, topk=topk)
        answer = self.generate_answer(query=query, hits=hits)
        return {
            "answer": answer,
            "evidence": [
                {"chunk_id": c.chunk_id, "doc_id": c.doc_id, "page_no": c.page_no, "text": c.text[:100]}
                for c, _ in hits
            ],
        }

    def retrieve(self, query: str, topk: int = 3) -> List[Tuple[TextChunk, float]]:
        """
        检索：query 向量化后与 chunk 向量做相似度。
        """
        q_vec = self._embed(query)
        scored: List[Tuple[TextChunk, float]] = []
        for chunk in self.chunks:
            score = self._cosine_sim(q_vec, chunk.vector)
            scored.append((chunk, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:topk]

    def generate_answer(self, query: str, hits: List[Tuple[TextChunk, float]]) -> str:
        """
        生成：教学版用“证据拼接 + 规则选取”模拟 LLM 回答。
        """
        if not hits:
            return "未检索到相关证据。"

        # 在教学阶段，先把证据直接展示出来，帮助你理解“答案从何而来”。
        evidence_text = " ".join(chunk.text for chunk, _ in hits)
        if "采购单号" in query and "PO-" in evidence_text:
            start = evidence_text.find("PO-")
            return evidence_text[start : start + 8]
        if "负责人" in query:
            for name in ["张三", "李四", "王五"]:
                if name in evidence_text:
                    return name
        return f"基于证据回答：{evidence_text[:80]}..."

    # ---------- 内部工具 ----------
    def _build_chunks(self, pages: Sequence[OcrPage]) -> List[TextChunk]:
        result: List[TextChunk] = []
        for page in pages:
            text = page.text.strip()
            if not text:
                continue
            idx = 0
            start = 0
            step = max(1, self.chunk_size - self.overlap)
            while start < len(text):
                part = text[start : start + self.chunk_size]
                chunk_id = f"{page.doc_id}-p{page.page_no}-c{idx}"
                result.append(
                    TextChunk(
                        chunk_id=chunk_id,
                        doc_id=page.doc_id,
                        page_no=page.page_no,
                        text=part,
                    )
                )
                idx += 1
                start += step
        return result

    def _embed(self, text: str) -> List[float]:
        """
        用哈希向量模拟 embedding，方便你理解“文本 -> 向量”这件事。
        """
        vec = [0.0] * self.vector_dim
        tokens = text.lower().replace("，", " ").replace("。", " ").split()
        for token in tokens:
            h = hashlib.sha256(token.encode("utf-8")).hexdigest()
            bucket = int(h[:8], 16) % self.vector_dim
            vec[bucket] += 1.0
        norm = sum(v * v for v in vec) ** 0.5
        if norm == 0:
            return vec
        return [v / norm for v in vec]

    @staticmethod
    def _cosine_sim(a: Sequence[float], b: Sequence[float]) -> float:
        if not a or not b:
            return 0.0
        dim = min(len(a), len(b))
        return float(sum(a[i] * b[i] for i in range(dim)))

