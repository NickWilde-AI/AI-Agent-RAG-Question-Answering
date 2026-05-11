"""
检索模块（模拟“多模态页面 embedding + 向量检索”）。

说明：
- 为了本地可运行，这里没有接真实图像模型，而是用文本哈希来模拟 embedding。
- 这不影响你讲“系统工程结构”，后续只需替换 embed 函数和索引实现。

如果你是 Java 后端背景，可以这样类比：
- PageRetriever 相当于“Milvus/ES Client 的封装 + 召回策略层”
- _build_index 相当于离线建库 Job（Spark/Flink/批处理脚本）
- retrieve 相当于在线请求的“召回服务”入口（会做改写、过滤、排序）
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .config import SETTINGS
from .infra.vector_store import BaseVectorStore, InMemoryVectorStore, MilvusVectorStore
from .llm_client import LLMClient
from .models import Page, RetrievalHit
from .services import ColPaliRerankClient, MultimodalEmbeddingClient


def _normalize(vec: np.ndarray) -> np.ndarray:
    """
    向量归一化，便于使用余弦相似度。

    解释一下“余弦相似度”：
    - 我们把文本/图片编码成向量 v
    - 两个向量夹角越小，余弦值越大，说明越相似
    - 归一化后，点积 dot(a, b) 就等价于余弦相似度（更快）
    """
    norm = np.linalg.norm(vec)
    if norm == 0:
        return vec
    return vec / norm


def _hash_to_vector(text: str, dim: int) -> np.ndarray:
    """
    用哈希模拟 embedding（演示用，保证离线可跑）。

    原理（你只要理解“把文本变成一个可比较的向量”即可，不用纠结哈希细节）：
    - 将文本分词后对每个 token 做 sha256
    - 将哈希值映射到固定维度桶
    - 统计桶计数后归一化
    """
    vec = np.zeros(dim, dtype=np.float32)
    tokens = text.lower().replace("，", " ").replace("。", " ").split()
    if not tokens:
        return vec

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) % dim
        vec[bucket] += 1.0

    return _normalize(vec)


def _safe_dot(a: np.ndarray, b: np.ndarray) -> float:
    """处理不同维度向量的兜底点积，避免线上崩溃。"""
    if a.shape[0] == b.shape[0]:
        return float(np.dot(a, b))
    dim = min(a.shape[0], b.shape[0])
    if dim == 0:
        return 0.0
    return float(np.dot(a[:dim], b[:dim]))


def _tokenize(text: str) -> List[str]:
    """
    中英混合的轻量分词（演示版）。

    为什么需要 tokenize：
    - 我们后面做“词面匹配(lexical)”得分
    - 这能让 demo 在小数据集上更稳定、更可解释
    """
    normalized = (
        text.lower()
        .replace("，", " ")
        .replace("。", " ")
        .replace("：", " ")
        .replace("；", " ")
        .replace("/", " ")
        .replace("-", " ")
    )
    tokens = [t for t in normalized.split() if t]
    # 中文连续片段（人名、术语等），避免仅靠空格分词导致召回为 0
    zh_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    for z in zh_chunks:
        if z not in tokens:
            tokens.append(z)
    alnum = re.findall(r"[a-z0-9]{2,}", normalized)
    for a in alnum:
        if a not in tokens:
            tokens.append(a)
    return tokens


class PageRetriever:
    """
    页面级检索器。

    你可以把它对应到真实系统中的：
    - build_index: 离线建库（渲染页面 -> embedding -> 向量入库）
    - retrieve: 在线召回（query rewrite -> 向量搜索 -> topk）
    """

    def __init__(
        self,
        pages: List[Page],
        vector_dim: int = SETTINGS.vector_dim,
        llm_client: Optional[LLMClient] = None,
        vector_store: Optional[BaseVectorStore] = None,
    ) -> None:
        self.pages = pages
        self.vector_dim = vector_dim
        self.llm_client = llm_client
        self.multimodal_client = MultimodalEmbeddingClient()
        self.rerank_client = ColPaliRerankClient()
        self.vector_store = vector_store or self._build_vector_store()
        self.page_vectors: Dict[str, np.ndarray] = {}
        self.id_to_page: Dict[str, Page] = {p.page_id: p for p in pages}
        self._build_index()

    @staticmethod
    def from_json(path: str | Path, llm_client: Optional[LLMClient] = None) -> "PageRetriever":
        """从 JSON 文件加载页面数据并创建检索器。"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        pages = [Page(**item) for item in data]
        return PageRetriever(pages=pages, llm_client=llm_client)

    def _build_vector_store(self) -> BaseVectorStore:
        """按配置选择 Milvus 或内存向量库。"""
        if SETTINGS.vector_backend == "milvus":
            try:
                return MilvusVectorStore(
                    uri=SETTINGS.milvus_uri,
                    token=SETTINGS.milvus_token,
                    collection=SETTINGS.milvus_collection,
                    dim=self.vector_dim,
                )
            except Exception:
                return InMemoryVectorStore()
        return InMemoryVectorStore()

    def _embed_text(self, text: str) -> np.ndarray:
        """优先走真实 embedding，失败时自动降级哈希 embedding。"""
        if self.multimodal_client.enabled:
            try:
                return _normalize(np.array(self.multimodal_client.embed_text(text), dtype=np.float32))
            except Exception:
                pass
        if SETTINGS.enable_real_embedding and self.llm_client and self.llm_client.enabled:
            try:
                vec = np.array(self.llm_client.embed(text), dtype=np.float32)
                return _normalize(vec)
            except Exception:
                # 线上可在此接入日志与告警；demo 里保持静默降级可运行。
                pass
        return _hash_to_vector(text, self.vector_dim)

    def _embed_page(self, page: Page) -> np.ndarray:
        """优先用页图 embedding；没有图像或外部服务失败时回退文本 embedding。"""
        if page.image_path and self.multimodal_client.enabled:
            try:
                vec = np.array(
                    self.multimodal_client.embed_image(
                        image_path=page.image_path,
                        text_hint=f"{page.doc_type} {page.language} {page.content[:200]}",
                    ),
                    dtype=np.float32,
                )
                return _normalize(vec)
            except Exception:
                pass
        text_for_embed = f"{page.doc_type} {page.language} {page.content}"
        return self._embed_text(text_for_embed)

    def _build_index(self) -> None:
        """
        离线建索引（演示版）。

        真实简历里的建库链路是：
        PDF/PPT 渲染页图 -> 多模态 embedding(ColPali/MiniCPM-V) -> 向量入 Milvus -> 文件名映射

        这里为了可运行，把“页图 embedding”用 `page.content` 的 hash 向量模拟。
        你面试时可以说：接口和流程是对齐的，只是底层实现是 mock。
        """
        for page in self.pages:
            vec = self._embed_page(page)
            self.page_vectors[page.page_id] = vec
            self.vector_store.upsert(page.page_id, vec.tolist())

    def rewrite_query(self, query: str) -> str:
        """
        Query 改写（简化版）。

        真实场景会使用（你简历里写的“query rewrite + 术语词典”就在这里）：
        - 术语词典
        - 业务别名归一
        - LLM 改写
        """
        if not SETTINGS.enable_query_rewrite:
            return query

        replacements = {
            "负责人": "负责人 owner owner",
            "销售额": "销售额 revenue",
            "故障代码": "故障代码 error_code",
            "中文含义": "中文 含义 翻译",
        }
        rewritten = query
        for src, dst in replacements.items():
            rewritten = rewritten.replace(src, dst)
        return rewritten

    @staticmethod
    def infer_doc_type(query: str) -> Optional[str]:
        """
        由 query 推断文档类型，模拟“类型预过滤”。

        为什么要预过滤（很重要的工程点）：
        - 全库检索时噪声很大，top-k 里混入错误类型页面，下游更容易答偏
        - 先用轻量规则缩小候选集合，能明显提升召回质量和端到端准确率
        - 这也是你简历里“工程优化 ROI 高于训模型”的典型例子
        """
        q = query.lower()
        if any(x in q for x in ["采购", "单号", "表单", "发票"]):
            return "form"
        if any(x in q for x in ["销售额", "图表", "柱状", "趋势", "报表"]):
            return "report"
        if any(x in q for x in ["介绍", "试点", "汇报", "ppt", "跨页"]):
            return "ppt"
        if any(x in q for x in ["故障代码", "中文含义", "翻译", "英文", "外文"]):
            return "manual"
        return None

    def retrieve(self, query: str, topk: int = SETTINGS.topk_default, doc_type: Optional[str] = None) -> Tuple[str, List[RetrievalHit]]:
        """
        在线检索（你可以把它当成“召回服务”的主逻辑）。

        返回：
        1) rewritten_query：便于打印和调试
        2) hits：按相似度排序后的 top-k 页面
        """
        # 1) Query rewrite：把“口语化/带别名”的 query 变得更标准
        rewritten_query = self.rewrite_query(query)

        # 2) 文档类型预过滤：缩小检索空间，减少噪声页进入 top-k
        inferred_doc_type = doc_type or self.infer_doc_type(query)

        # 3) embedding：把 query 编成向量（真实系统：文本 embedding / 多模态 embedding）
        q_vec = self._embed_text(rewritten_query)

        # 4) 词面 token：用于解释性更强的 lexical 匹配得分
        q_tokens = set(_tokenize(rewritten_query))

        candidate_pages = self.pages
        if not isinstance(self.vector_store, InMemoryVectorStore):
            candidate_ids = [item_id for item_id, _ in self.vector_store.search(q_vec.tolist(), topk=max(topk * 5, 20))]
            candidate_pages = [self.id_to_page[item_id] for item_id in candidate_ids if item_id in self.id_to_page]

        scored: List[RetrievalHit] = []
        for page in candidate_pages:
            if inferred_doc_type and page.doc_type != inferred_doc_type:
                continue

            # 5) 语义分：向量相似度（演示版：点积；真实版：Milvus HNSW + cosine）
            vec_score = _safe_dot(q_vec, self.page_vectors[page.page_id])

            # 6) 词面分：query token 与页面 token 的重叠比例（越大说明越像“字面相关”）
            page_tokens = set(_tokenize(page.content + " " + " ".join(page.fields.keys())))
            overlap = len(q_tokens & page_tokens)
            lexical_score = overlap / max(len(q_tokens), 1)

            # 7) 混合评分：语义 + 词面
            # 真实系统里你还可以加：
            # - rerank（例如 ColPali late-interaction）
            # - 业务权重（近期文档加权、文档权限等）
            score = 0.4 * vec_score + 0.6 * lexical_score
            q_plain = rewritten_query.strip()
            if q_plain and q_plain in (page.content or ""):
                score += 0.35
            scored.append(RetrievalHit(page_id=page.page_id, score=score))

        scored.sort(key=lambda x: x.score, reverse=True)
        if self.rerank_client.enabled and scored:
            try:
                rerank_candidates = []
                cap = min(max(topk * 3, topk), SETTINGS.colpali_rerank_max_pages * 2)
                for hit in scored[:cap]:
                    page = self.id_to_page[hit.page_id]
                    if page.image_path:
                        rerank_candidates.append(
                            {
                                "page_id": page.page_id,
                                "image_path": page.image_path,
                                "doc_id": page.doc_id,
                            }
                        )
                rerank_scores = self.rerank_client.rerank_pages(rewritten_query, rerank_candidates)
                scored = [
                    RetrievalHit(page_id=h.page_id, score=0.5 * h.score + 0.5 * rerank_scores.get(h.page_id, h.score))
                    for h in scored
                ]
                scored.sort(key=lambda x: x.score, reverse=True)
            except Exception:
                pass
        return rewritten_query, scored[:topk]

    def get_page(self, page_id: str) -> Page:
        """根据 page_id 取页面对象。"""
        return self.id_to_page[page_id]
