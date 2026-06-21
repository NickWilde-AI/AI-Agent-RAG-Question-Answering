"""
retriever.py — L0 检索：query → top-k 页面（向量 + 规则混合，可接 Milvus / 多模态 embedding）

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
- `PageRetriever.retrieve`：`pipeline` 第 1 步与 fallback 第 2 次检索都走这里。
- `rewrite_query`：对应简历「query rewrite + 术语词典」的 demo 版（字符串替换）。
- `infer_doc_type`：对应「文档类型预过滤」的轻量推断。
- `_embed_page` / `_build_index`：演示可接 **MultimodalEmbeddingClient**（ColPali/MiniCPM-V HTTP）；失败则哈希向量，保证离线可跑。

================================================================================
【类比 Android】
================================================================================
- `PageRetriever` ≈ **Repository**：对上暴露 `retrieve`，对下接「向量库 Client + 外部 Embedding HTTP」。
- `_build_index` ≈ WorkManager **一次性同步任务**：启动时把 JSON 页面向量化写入 `vector_store`。
- `numpy` 向量点积 ≈ `float[]` 做 cosine 前的归一化点积，数学同构。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `path: str | Path`：联合类型参数；也可用 `Union[str, Path]`。
- `Page(**item)`：**字典解包**成关键字参数，类似 Gson `fromJson` 映射到 data class 构造器（字段名需一致）。
- `[Page(**item) for item in data]`：列表推导，≈ `stream().map(...).toList()`。
- `np.ndarray` / `dtype=np.float32`：NumPy 强类型浮点数组，类似 `float[]` 但带 shape。

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
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
from prometheus_client import Counter as PromCounter
import sentry_sdk

from .config import SETTINGS

BM25_FALLBACK_COUNT = PromCounter(
    "rag_bm25_fallback_total",
    "Retrieval used BM25 fallback scoring",
)
from .infra.vector_store import BaseVectorStore, InMemoryVectorStore, MilvusVectorStore
from .infra.embedding_cache import JSONEmbeddingCache
from .llm_client import LLMClient
from .models import Page, RetrievalHit
from .services import ColPaliRerankClient, MultimodalEmbeddingClient, VLMClient


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
        # 中文没有天然空格，补充短 n-gram，避免“采购申请单的采购单号”和“采购申请单。采购单号”无法重叠。
        max_n = min(4, len(z))
        for n in range(2, max_n + 1):
            for i in range(0, len(z) - n + 1):
                gram = z[i : i + n]
                if gram not in tokens:
                    tokens.append(gram)
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
        self.visual_rerank_client = VLMClient()
        self.vector_store = vector_store or self._build_vector_store()
        self.page_vectors: Dict[str, np.ndarray] = {}
        self.id_to_page: Dict[str, Page] = {p.page_id: p for p in pages}
        self._external_embed_failed = False
        self._embedding_cache = JSONEmbeddingCache(SETTINGS.embedding_cache_path,SETTINGS.embedding_cache_max_entries)
        self._embedding_cache_hits = 0
        self._building_index = False
        self._page_token_counters: Dict[str, Counter] = {}
        self._doc_freq: Dict[str, int] = defaultdict(int)
        self._avg_doc_len = 1.0
        self.last_diagnostics: Dict[str, str] = {}
        self._build_bm25_stats()
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
            except Exception as exc:
                if SETTINGS.sentry_dsn:
                    with sentry_sdk.push_scope() as scope:
                        scope.set_tag("component", "retriever")
                        scope.set_tag("phase", "milvus_init")
                        sentry_sdk.capture_exception(exc)
                return InMemoryVectorStore()
        return InMemoryVectorStore()

    def _embed_text(self, text: str) -> np.ndarray:
        """优先走真实 embedding，失败时自动降级哈希 embedding。"""
        self._external_embed_failed = False
        if self.multimodal_client.enabled:
            try:
                return _normalize(np.array(self.multimodal_client.embed_text(text), dtype=np.float32))
            except Exception as exc:
                self._external_embed_failed = True
                if SETTINGS.sentry_dsn:
                    with sentry_sdk.push_scope() as scope:
                        scope.set_tag("component", "retriever")
                        scope.set_tag("phase", "embed_text_multimodal")
                        sentry_sdk.capture_exception(exc)
        if SETTINGS.enable_real_embedding and self.llm_client and self.llm_client.enabled:
            cache_key = self._embedding_cache.key(
                SETTINGS.openai_base_url, SETTINGS.openai_embedding_model, text
            )
            if SETTINGS.enable_embedding_cache:
                cached=self._embedding_cache.get(cache_key)
                if cached is not None:
                    self._embedding_cache_hits += 1
                    return _normalize(np.array(cached,dtype=np.float32))
            try:
                raw_vec=self.llm_client.embed(text)
                if SETTINGS.enable_embedding_cache:
                    self._embedding_cache.put(cache_key,raw_vec)
                    if not self._building_index: self._embedding_cache.save()
                vec = np.array(raw_vec, dtype=np.float32)
                return _normalize(vec)
            except Exception as exc:
                self._external_embed_failed = True
                # 线上可在此接入日志与告警；demo 里保持静默降级可运行。
                if SETTINGS.sentry_dsn:
                    with sentry_sdk.push_scope() as scope:
                        scope.set_tag("component", "retriever")
                        scope.set_tag("phase", "embed_text_llm")
                        sentry_sdk.capture_exception(exc)
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
            except Exception as exc:
                self._external_embed_failed = True
                if SETTINGS.sentry_dsn:
                    with sentry_sdk.push_scope() as scope:
                        scope.set_tag("component", "retriever")
                        scope.set_tag("phase", "embed_page_image")
                        sentry_sdk.capture_exception(exc)
        text_for_embed = f"{page.doc_type} {page.language} {page.content}"
        return self._embed_text(text_for_embed)

    def _build_bm25_stats(self) -> None:
        if not self.pages:
            return
        total_len = 0
        for page in self.pages:
            tokens = _tokenize((page.content or "") + " " + " ".join((page.fields or {}).keys()))
            if not tokens:
                tokens = ["_empty_"]
            counter = Counter(tokens)
            self._page_token_counters[page.page_id] = counter
            total_len += sum(counter.values())
            for t in counter.keys():
                self._doc_freq[t] += 1
        self._avg_doc_len = max(total_len / len(self.pages), 1.0)

    def _bm25_score_page(self, query_tokens: List[str], page_id: str) -> float:
        counter = self._page_token_counters.get(page_id)
        if not counter:
            return 0.0
        dl = sum(counter.values()) or 1
        n_docs = max(len(self.pages), 1)
        k1 = SETTINGS.bm25_k1
        b = SETTINGS.bm25_b
        score = 0.0
        for t in query_tokens:
            tf = counter.get(t, 0)
            if tf <= 0:
                continue
            df = self._doc_freq.get(t, 0)
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            denom = tf + k1 * (1 - b + b * dl / self._avg_doc_len)
            score += idf * (tf * (k1 + 1) / max(denom, 1e-6))
        return float(score)

    def _build_index(self) -> None:
        """
        离线建索引（演示版）。

        真实简历里的建库链路是：
        PDF/PPT 渲染页图 -> 多模态 embedding(ColPali/MiniCPM-V) -> 向量入 Milvus -> 文件名映射

        这里为了可运行，把“页图 embedding”用 `page.content` 的 hash 向量模拟。
        你面试时可以说：接口和流程是对齐的，只是底层实现是 mock。
        """
        total = len(self.pages)
        use_remote = bool(
            SETTINGS.enable_real_embedding
            and self.llm_client
            and self.llm_client.enabled
        ) or self.multimodal_client.enabled
        if total > 20 and use_remote:
            print(
                f"[PageRetriever] 正在为 {total} 页构建向量（远程 embedding 较慢，完成后才会开放 /health）…",
                flush=True,
            )
        self._building_index=True
        try:
            for i, page in enumerate(self.pages, start=1):
                vec = self._embed_page(page)
                self.page_vectors[page.page_id] = vec
                self.vector_store.upsert(page.page_id, vec.tolist())
                if use_remote and total > 20 and (i == 1 or i % 50 == 0 or i == total):
                    print(f"[PageRetriever] 向量进度 {i}/{total}", flush=True)
        finally:
            self._building_index=False
            if SETTINGS.enable_embedding_cache: self._embedding_cache.save()
        if total > 20 and use_remote:
            print(f"[PageRetriever] 向量索引完成，共 {total} 页，缓存命中 {self._embedding_cache_hits}", flush=True)

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
        }
        rewritten = query
        for src, dst in replacements.items():
            rewritten = rewritten.replace(src, dst)
        return rewritten

    def _query_variants(self, query: str, rewritten_query: str) -> List[str]:
        """
        轻量 query expansion。

        现代 Agentic RAG 通常会先分析问题，把复合问题拆成多个检索意图；这里不依赖 LLM，
        用规则产生少量稳定变体，再交给 RRF 融合，避免一次改写漏掉关键证据页。
        """
        variants: List[str] = []

        def add(text: str) -> None:
            normalized = " ".join((text or "").strip().split())
            if normalized and normalized not in variants:
                variants.append(normalized)

        add(rewritten_query)
        add(query)
        if not SETTINGS.enable_query_expansion:
            return variants[:1]

        separators = r"[，,。；;？?\n]|(?:以及)|(?:并且)|(?:同时)|(?:分别)|(?:对比)|(?:和)|(?:与)"
        for part in re.split(separators, rewritten_query):
            part = part.strip()
            if len(part) >= 4:
                add(part)

        # 抽取“文档名/字段名/编号/实体”式关键词，提升表单、报表和 PPT 的精确召回。
        keyword_parts: List[str] = []
        raw_keywords = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|\d{2,}|[\u4e00-\u9fff]{2,}", rewritten_query)
        for token in raw_keywords:
            if len(token) >= 2 and token not in {"多少", "什么", "哪个", "如何", "请问"}:
                keyword_parts.append(token)
        if keyword_parts:
            add(" ".join(keyword_parts[:8]))

        return variants[: max(1, SETTINGS.max_query_variants)]

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
        return None

    def _candidate_pages_for_query(self, q_vec: np.ndarray, topk: int) -> List[Page]:
        if isinstance(self.vector_store, InMemoryVectorStore):
            return self.pages
        candidate_ids = [
            item_id
            for item_id, _ in self.vector_store.search(
                q_vec.tolist(),
                topk=max(topk * max(SETTINGS.max_query_variants, 1) * 5, 20),
            )
        ]
        return [self.id_to_page[item_id] for item_id in candidate_ids if item_id in self.id_to_page]

    def _score_pages(
        self,
        query_text: str,
        q_vec: np.ndarray,
        query_tokens: Iterable[str],
        candidate_pages: List[Page],
        inferred_doc_type: Optional[str],
        use_bm25: bool,
    ) -> List[RetrievalHit]:
        q_tokens = set(query_tokens)
        scored: List[RetrievalHit] = []
        for page in candidate_pages:
            if inferred_doc_type and page.doc_type != inferred_doc_type:
                continue

            # 语义分：向量相似度（演示版：点积；真实版：Milvus HNSW + cosine）
            vec_score = _safe_dot(q_vec, self.page_vectors[page.page_id])

            # 词面分：query token 与页面 token 的重叠比例（越大说明越像“字面相关”）
            page_tokens = set(_tokenize(page.content + " " + " ".join(page.fields.keys())))
            overlap = len(q_tokens & page_tokens)
            lexical_score = overlap / max(len(q_tokens), 1)

            # 混合评分：语义 + 词面（外部 embedding 异常时可融合 BM25 兜底）
            score = 0.4 * vec_score + 0.6 * lexical_score
            if use_bm25:
                bm25 = self._bm25_score_page(list(q_tokens), page.page_id)
                score = 0.15 * vec_score + 0.25 * lexical_score + 0.60 * bm25
            q_plain = query_text.strip()
            if q_plain and q_plain in (page.content or ""):
                score += 0.35
            # “第 N 页 / p.N”是通用检索约束，不绑定任何具体文档；只做加权，不做全库硬过滤。
            requested_pages = {
                int(value)
                for value in re.findall(r"(?:第\s*|p\.?\s*)(\d{1,5})\s*页?", query_text, flags=re.I)
            }
            if requested_pages and page.page_no in requested_pages:
                score += 0.75
            scored.append(RetrievalHit(page_id=page.page_id, score=score))
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored

    def _fuse_ranked_hits(self, ranked_lists: List[List[RetrievalHit]]) -> List[RetrievalHit]:
        if not ranked_lists:
            return []
        max_raw: Dict[str, float] = defaultdict(float)
        rrf_scores: Dict[str, float] = defaultdict(float)
        first_rank: Dict[str, int] = {}
        for hits in ranked_lists:
            for rank, hit in enumerate(hits, start=1):
                max_raw[hit.page_id] = max(max_raw[hit.page_id], hit.score)
                rrf_scores[hit.page_id] += 1.0 / (SETTINGS.rrf_k + rank)
                first_rank[hit.page_id] = min(first_rank.get(hit.page_id, rank), rank)

        max_rrf = max(rrf_scores.values()) if rrf_scores else 1.0
        fused = [
            RetrievalHit(
                page_id=page_id,
                score=(
                    0.0
                    if raw_score <= 1e-6
                    else (0.65 * raw_score) + (0.35 * (rrf_scores[page_id] / max_rrf))
                ),
            )
            for page_id, raw_score in max_raw.items()
        ]
        fused.sort(key=lambda x: (x.score, -first_rank.get(x.page_id, 10**9)), reverse=True)
        return fused

    def _apply_doc_diversity(self, hits: List[RetrievalHit], limit_per_doc: int) -> List[RetrievalHit]:
        if limit_per_doc <= 0:
            return hits
        doc_counts: Dict[str, int] = defaultdict(int)
        selected: List[RetrievalHit] = []
        deferred: List[RetrievalHit] = []
        for hit in hits:
            page = self.id_to_page.get(hit.page_id)
            doc_id = page.doc_id if page else hit.page_id
            if doc_counts[doc_id] < limit_per_doc:
                selected.append(hit)
                doc_counts[doc_id] += 1
            else:
                deferred.append(hit)
        return selected + deferred

    def _hierarchical_page_candidates(self,hits: List[RetrievalHit]) -> List[RetrievalHit]:
        """先按文档聚合粗排，再在高相关文档内保留页面；为未来独立文档索引保留同一边界。"""
        if not SETTINGS.enable_hierarchical_retrieval or not hits:
            return hits
        by_doc: Dict[str,List[RetrievalHit]] = defaultdict(list)
        for hit in hits:
            page=self.id_to_page.get(hit.page_id)
            by_doc[page.doc_id if page else hit.page_id].append(hit)
        doc_scores=[]
        for doc_id,doc_hits in by_doc.items():
            ordered=sorted(doc_hits,key=lambda item:item.score,reverse=True)
            score=ordered[0].score + 0.15 * sum(item.score for item in ordered[1:3])
            doc_scores.append((doc_id,score))
        doc_scores.sort(key=lambda item:item[1],reverse=True)
        doc_limit=max(1,SETTINGS.retrieval_candidate_docs)
        selected_docs={doc_id for doc_id,_ in doc_scores[:doc_limit]}
        selected=[hit for hit in hits if (self.id_to_page.get(hit.page_id).doc_id if self.id_to_page.get(hit.page_id) else hit.page_id) in selected_docs]
        deferred=[hit for hit in hits if hit not in selected]
        selected.sort(key=lambda hit:hit.score,reverse=True)
        return selected + deferred

    @staticmethod
    def _blend_rerank_scores(candidates: List[RetrievalHit],scores: Dict[str,float]) -> List[RetrievalHit]:
        """把不同模型的绝对分数转成稳定的名次分再融合，避免 ColPali/Qwen 分数尺度不一致。"""
        if not candidates or not scores: return candidates
        visual_order=sorted(scores,key=lambda page_id:scores[page_id],reverse=True)
        visual_rank={page_id:rank for rank,page_id in enumerate(visual_order,1)}
        total=max(len(candidates),1); weight=min(max(SETTINGS.visual_rerank_weight,0.0),1.0)
        blended=[]
        for coarse_rank,hit in enumerate(candidates,1):
            coarse=1.0-(coarse_rank-1)/total
            rank=visual_rank.get(hit.page_id,total+1)
            visual=0.0 if rank>total else 1.0-(rank-1)/total
            blended.append(RetrievalHit(hit.page_id,(1.0-weight)*coarse+weight*visual))
        blended.sort(key=lambda hit:hit.score,reverse=True)
        return blended

    @staticmethod
    def _query_anchor_terms(query: str) -> List[str]:
        """抽取产品名、缩写、编号等高区分度锚点，用于视觉重排候选的精确召回配额。"""
        patterns=(
            r"[\u4e00-\u9fff]{1,8}[A-Za-z][A-Za-z0-9+._/-]*",
            r"[A-Za-z][A-Za-z0-9+._/-]*[\u4e00-\u9fff]{1,8}",
            r"[A-Za-z0-9]+(?:[+._/-][A-Za-z0-9]+)+",
            r"[A-Za-z][A-Za-z0-9+._/-]{1,}",
            r"[A-Z]{2,}[A-Z0-9_-]*",
        )
        terms=[]
        for pattern in patterns:
            for term in re.findall(pattern,query):
                normalized=term.strip()
                if len(normalized)>=2 and normalized.lower() not in {x.lower() for x in terms}:
                    terms.append(normalized)
        return terms

    def _visual_rerank_shortlist(self,query: str,candidates: List[RetrievalHit],cap: int) -> List[RetrievalHit]:
        """混合采样：一半取融合粗排，一半取精确锚点/BM25，避免小 API 配额只看到同类噪声。"""
        if cap<=0 or len(candidates)<=cap: return candidates[:max(cap,0)]
        coarse_n=max(1,cap//2); selected=list(candidates[:coarse_n]); selected_ids={hit.page_id for hit in selected}
        anchors=self._query_anchor_terms(query)
        query_tokens=_tokenize(query)
        lexical=[]
        for hit in candidates:
            if hit.page_id in selected_ids: continue
            page=self.id_to_page[hit.page_id]; content=(page.content or "").lower()
            exact=sum(1 for term in anchors if term.lower() in content)
            bm25=self._bm25_score_page(query_tokens,hit.page_id)
            lexical.append((exact,bm25,hit.score,hit))
        lexical.sort(key=lambda item:(item[0],item[1],item[2]),reverse=True)
        selected.extend(item[3] for item in lexical[:cap-len(selected)])
        return selected

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

        query_variants = self._query_variants(query, rewritten_query)

        variant_vectors: List[Tuple[str, np.ndarray]] = []
        external_embed_failed = False
        for variant in query_variants:
            q_vec = self._embed_text(variant)
            external_embed_failed = external_embed_failed or self._external_embed_failed
            variant_vectors.append((variant, q_vec))
        use_bm25 = SETTINGS.enable_hybrid_bm25 or (SETTINGS.enable_bm25_fallback and external_embed_failed)
        if SETTINGS.enable_bm25_fallback and external_embed_failed:
            BM25_FALLBACK_COUNT.inc()

        ranked_lists: List[List[RetrievalHit]] = []
        for variant, q_vec in variant_vectors:
            candidate_pages = self._candidate_pages_for_query(q_vec, topk)
            ranked_lists.append(
                self._score_pages(
                    query_text=variant,
                    q_vec=q_vec,
                    query_tokens=_tokenize(variant),
                    candidate_pages=candidate_pages,
                    inferred_doc_type=inferred_doc_type,
                    use_bm25=use_bm25,
                )
            )

        scored = self._hierarchical_page_candidates(self._fuse_ranked_hits(ranked_lists))
        candidate_limit=max(topk,SETTINGS.retrieval_candidate_pages)
        candidate_pool=scored[:candidate_limit]
        rerank_backend=""
        if (self.rerank_client.enabled or (SETTINGS.enable_visual_rerank and self.visual_rerank_client.enabled)) and candidate_pool:
            try:
                rerank_candidates = []
                if self.rerank_client.enabled:
                    cap=min(len(candidate_pool),max(1,SETTINGS.colpali_rerank_max_pages)); rerank_backend="colpali"
                else:
                    cap=min(len(candidate_pool),max(1,SETTINGS.visual_rerank_candidate_pages)); rerank_backend="qwen_api"
                rerank_hits=self._visual_rerank_shortlist(query,candidate_pool,cap)
                for hit in rerank_hits:
                    page = self.id_to_page[hit.page_id]
                    if page.image_path:
                        rerank_candidates.append(
                            {
                                "page_id": page.page_id,
                                "image_path": page.image_path,
                                "doc_id": page.doc_id,
                                "source_file": Path(page.source_file).name if page.source_file else page.doc_id,
                                "page_no": page.page_no,
                            }
                        )
                if self.rerank_client.enabled:
                    rerank_scores=self.rerank_client.rerank_pages(query,rerank_candidates)
                else:
                    rerank_scores=self.visual_rerank_client.rerank_pages(query,rerank_candidates)
                reranked=self._blend_rerank_scores(rerank_hits,rerank_scores)
                reranked_ids={hit.page_id for hit in reranked}
                candidate_pool=reranked+[hit for hit in candidate_pool if hit.page_id not in reranked_ids]
                candidate_ids={hit.page_id for hit in candidate_pool}
                scored=candidate_pool+[hit for hit in scored if hit.page_id not in candidate_ids]
            except Exception as exc:
                if SETTINGS.sentry_dsn:
                    with sentry_sdk.push_scope() as scope:
                        scope.set_tag("component", "retriever")
                        scope.set_tag("phase", "colpali_rerank")
                        sentry_sdk.capture_exception(exc)
        scored = self._apply_doc_diversity(scored, SETTINGS.retrieval_diversity_per_doc)
        self.last_diagnostics = {
            "query_variants": str(len(query_variants)),
            "variants": " | ".join(query_variants),
            "doc_type": inferred_doc_type or "",
            "fusion": "rrf" if len(query_variants) > 1 else "single",
            "diversity_per_doc": str(SETTINGS.retrieval_diversity_per_doc),
            "hybrid_bm25": str(use_bm25).lower(),
            "bm25_fallback": str(SETTINGS.enable_bm25_fallback and external_embed_failed).lower(),
            "candidate_pages": str(min(len(scored),candidate_limit)),
            "candidate_docs": str(SETTINGS.retrieval_candidate_docs),
            "visual_rerank": rerank_backend or "disabled",
        }
        return rewritten_query, scored[:topk]

    def get_page(self, page_id: str) -> Page:
        """根据 page_id 取页面对象。"""
        return self.id_to_page[page_id]
