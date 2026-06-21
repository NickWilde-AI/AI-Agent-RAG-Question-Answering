"""
pipeline.py — 端到端编排（QAEngine）：简历里「Agent 主链路」的心脏

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
`QAEngine.ask` 固定执行：检索 -> 路由 -> 生成 -> 校验 -> 重试 -> 记忆。
"""

# ##############################################################################
# >>> Python 里没有「黄色注释」语法；用 # >>> 前缀块方便你在 IDE 里一眼扫到（可自行设关键字高亮）
# >>> 下面全是「读代码用」，不参与执行逻辑。
# ##############################################################################
#
# >>> self
# >>>   实例方法第一个参数，代表「当前对象」，类似 Java 里的 this（但必须显式写在参数表里）。
# >>>   调用时写成 obj.method(a) 时，Python 会自动把 obj 传给 self。
#
# >>> or（两种常见用法，和 Java 的 || 不完全一样）
# >>>   ① a or b：若 a「为假」（None、0、""、[] 等），结果是 b；否则结果是 a。常用来给默认值，类似 Kotlin 的 elvis 但要小心 0/""。
# >>>   ② if not verified: 里的 not：布尔取反，和 Java 的 ! 类似。
#
# >>> Optional[T]（来自 typing）
# >>>   表示「要么是 T，要么是 None」，接近 Java 的 Optional<T> / Kotlin 的 T?（但 Python 不会在运行时强制，主要靠类型检查）。
# >>>   本文件显式用得少，其它文件常见 Optional[str]、Optional[LLMClient]。
#
# >>> List[T]、str、bool
# >>>   类型标注，给 IDE/检查器看；运行时 Python 不校验（和 Java 泛型擦除不同，Python 是「根本不强制」）。
#
# >>> 方法名前导下划线 _xxx
# >>>   约定「类内部用的辅助方法」，不是 private（Python 没有真正的 private），只是告诉读者：别当公开 API 用。
#
# >>> from __future__ import annotations
# >>>   让类型注解里的名字晚点解析，减少「类里引用自己还没定义完」的问题；Kotlin/Java 一般不需要这行。
#
# ================================================================================
# 【从 Java/Kotlin 读 Python：本文件用到的语法】
# ================================================================================
# - `@dataclass` 里 `field(default_factory=list, init=False, repr=False)`：
#  - `init=False`：该字段**不由构造参数传入**，在 `__post_init__` 或首次使用前自己赋值；类似 Builder 里 internal state。
#  - `repr=False`：`repr(obj)` 里隐藏该字段，避免日志刷屏。
# - `Path(p.source_file).suffix`：取扩展名，类似 `FilenameUtils.getExtension`。
# - `{hp.page_id for hp in hit_pages}`：集合推导，≈ `hitPages.stream().map(Page::getPageId).collect(toSet())`。
# - `pages[: min(8, len(pages))]`：切片上界安全截断；`min` 防止越界。
# ##############################################################################

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from .config import SETTINGS
from .memory import SessionMemory
from .models import AgentTrace, Page, QAResult, RetrievalHit, StageTrace
from .retriever import PageRetriever
from .router import RouterAgent
from .tools import chart_qa, fact_qa, multi_page_qa
from .verifier import Verifier


@dataclass
class QAEngine:
    """
    QAEngine = 端到端问答引擎（编排器）。

    四个依赖分别对应：
    - retriever：检索器（L0），负责从知识库召回候选页面
    - router：路由器（L1），负责把问题分到不同工具链
    - memory：会话缓存（旁路），缓存历史结果降低重复成本
    - verifier：校验器（安全阀），判断答案是否“有证据”

    -------------------------------------------------------------------------
    【本类方法名 ↔ 白话（读代码时对照）】
    -------------------------------------------------------------------------
    ask                      → 「对外主流程」：问一句话，跑完检索→路由→生成→校验→可选重试
    _run_branch              → 「按题型去答题」：根据分支调用 fact / 多页 / 图表 / 翻译 工具
    _evidence_pages_for_verify → 「挑出要给校验器看的几页」：和生成用的页范围可能略有不同
    _expand_pages_for_evidence → 「把同一文档多页凑齐」：尤其 Excel 多 sheet；避免校验只看一页
    _source_file_names       → 「收集本次用到的文件名」：给接口返回 source_files，方便展示出处
    """

    retriever: PageRetriever
    router: RouterAgent
    memory: SessionMemory
    verifier: Verifier
    # 白话：最近一次「生成答案」时用到的源文件名列表；_run_branch 往里填，ask 末尾读出来写进 QAResult
    _last_source_files: List[str] = field(default_factory=list, init=False, repr=False)

    @staticmethod
    def _is_smalltalk_query(query: str) -> bool:
        """识别明显闲聊/自我指代问题，避免误触发知识库检索。"""
        q = (query or "").strip().lower()
        if not q:
            return True
        exact = {
            "你好",
            "hi",
            "hello",
            "在吗",
            "你是谁",
            "你的名字",
            "你的年龄",
            "你几岁",
            "介绍一下你自己",
        }
        if q in exact:
            return True
        return any(x in q for x in ["你是谁", "你的名字", "你的年龄", "你几岁"])

    @staticmethod
    def _fallback_smalltalk_answer() -> str:
        return (
            "我是企业知识库问答助手，擅长根据已入库文档回答业务问题。"
            "这类闲聊问题不在知识库检索范围内，请改为提问具体文档内容。"
        )

    @staticmethod
    def _query_terms_for_critique(query: str) -> List[str]:
        """抽取 evidence critique 使用的关键查询词，保持规则透明、便于企业验收解释。"""
        raw_terms = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|\d{2,}|[\u4e00-\u9fff]{2,}", query or "")
        stopwords = {
            "什么",
            "哪个",
            "哪些",
            "多少",
            "如何",
            "是否",
            "请问",
            "一下",
            "这个",
            "那个",
            "以及",
            "并且",
            "同时",
            "分别",
            "对比",
        }
        terms: List[str] = []
        for term in raw_terms:
            t = term.strip()
            if not t or t in stopwords or t.lower() in stopwords:
                continue
            if t not in terms:
                terms.append(t)
        return terms

    def _critique_retrieval(
        self,
        query: str,
        branch: str,
        answer: str,
        hits: List[RetrievalHit],
        evidence_pages: List[Page],
    ) -> dict:
        """
        Agentic RAG 的 Observe/Critique 步骤。

        它先判断当前证据为什么不够，再决定下一轮是扩召回还是改写 query 重搜。
        """
        evidence_text = " ".join(
            ((p.content or "") + " " + " ".join((p.fields or {}).keys()) + " " + " ".join((p.fields or {}).values()))
            for p in evidence_pages
        ).lower()
        missing_terms = [t for t in self._query_terms_for_critique(query) if t.lower() not in evidence_text]
        missing_terms = missing_terms[: SETTINGS.agentic_retry_max_missing_terms]

        reasons: List[str] = []
        if "材料中未找到足够依据" in answer or "暂未生成稳定归纳答案" in answer:
            reasons.append("answer_declared_insufficient")
        if missing_terms:
            reasons.append("query_terms_missing_in_evidence")
        if branch == RouterAgent.BRANCH_CHART and not any(p.chart_data for p in evidence_pages):
            reasons.append("chart_data_missing")
        if self._is_low_confidence(hits):
            reasons.append("low_confidence_hits")
        if not reasons:
            reasons.append("verifier_rejected_answer")

        return {
            "action": "refine_query" if SETTINGS.enable_agentic_retry_refine else "expand_topk",
            "reason": ",".join(reasons),
            "missing_terms": ",".join(missing_terms),
            "top_score": f"{hits[0].score:.4f}" if hits else "0",
        }

    @staticmethod
    def _refined_retry_query(query: str, branch: str, critique: dict) -> str:
        """根据 critique 结果生成第二轮检索 query。"""
        missing_terms = [x for x in str(critique.get("missing_terms", "")).split(",") if x]
        hints: List[str] = []
        if branch == RouterAgent.BRANCH_MULTI:
            hints.extend(["跨页", "多页", "负责人", "介绍", "汇报"])
        elif branch == RouterAgent.BRANCH_CHART:
            hints.extend(["图表", "数值", "指标", "最高", "报表"])
        else:
            hints.extend(["字段", "原文", "依据"])

        out: List[str] = []
        for item in [query, *missing_terms, *hints]:
            if item and item not in out:
                out.append(item)
        return " ".join(out)

    @staticmethod
    def _is_low_confidence(hits: List[RetrievalHit]) -> bool:
        """
        命中置信度过低时直接拒答，避免把无关材料拼接成长摘录。
        分值阈值按当前混合打分经验值设置，后续可在评测集上再调优。
        """
        if not hits:
            return True
        top1 = hits[0].score
        # top1 偏低且与第二名差距很小，通常代表“找不到明确证据页”
        if top1 < 0.35:
            return True
        if len(hits) >= 2 and top1 < 0.45 and abs(top1 - hits[1].score) < 0.03:
            return True
        return False

    def _source_file_names(self, pages: List[Page]) -> List[str]:
        """
        白话：从一批 Page 里整理出「人类可读的文件名」列表（去重），给接口展示「依据了哪些文件」。

        为什么单独一个方法：Page 上有的用 source_file 路径，有的只有 metadata 里的文件名，这里统一扒出来。
        """
        out: List[str] = []
        for p in pages:
            if p.source_file:
                n = Path(p.source_file).name
            else:
                meta = p.metadata or {}
                alt = meta.get("source_filename") or meta.get("original_filename") or meta.get("file_name")
                n = Path(str(alt)).name if alt else p.doc_id
            if n not in out:
                out.append(n)
        return out

    def _citation_details(self, hits: List[RetrievalHit], limit: int = 5) -> List[dict]:
        """生成轻量引用详情，供 API/验收回放展示，不改变生成模型输入。"""
        details: List[dict] = []
        for hit in hits[:limit]:
            page = self.retriever.get_page(hit.page_id)
            fname = self._source_file_names([page])[0] if page else hit.page_id
            excerpt = " ".join((page.content or "").split())[:180]
            details.append(
                {
                    "page_id": hit.page_id,
                    "doc_id": page.doc_id,
                    "source_file": fname,
                    "page_no": "" if page.page_no is None else str(page.page_no),
                    "score": f"{hit.score:.4f}",
                    "excerpt": excerpt,
                }
            )
        return details

    def _expand_pages_for_evidence(self, hit_pages: List[Page]) -> List[Page]:
        """
        白话：检索只命中了几页，但「答题 / 校验」有时需要**同一文件里更多页**一起看。

        - 普通文档先保留全部 top-k 命中，再按各命中补相邻页，避免第一份文档吞掉跨文档证据。
        - 表格仍按第一命中文件合并全部 sheet。
        - Excel/CSV：表格题常要跨 sheet，最多取 80 页一起当材料。
        - 普通 PDF 等：命中页放前面，同文档其余页放后面，总共最多 8 页（控制长度）。
        """
        if not hit_pages:
            return []
        p0 = hit_pages[0]
        ext = Path(p0.source_file).suffix.lower() if p0.source_file else ""
        merged = [p for p in self.retriever.pages if p.doc_id == p0.doc_id]
        merged.sort(key=lambda x: (x.page_no is None, x.page_no or 0))
        if ext in {".xlsx", ".xls", ".csv"}:
            return merged[:80]
        # 先完整保留跨文档 top-k 命中；旧实现只展开第一名所属文档，会把后续正确命中静默丢掉。
        ordered: List[Page] = []
        seen=set()
        for page in hit_pages:
            if page.page_id not in seen:
                ordered.append(page); seen.add(page.page_id)
        # 再为每个命中补相邻页面，支持上下文连续性，同时避免第一份文档垄断证据窗口。
        for anchor in hit_pages:
            neighbors=[p for p in self.retriever.pages if p.doc_id==anchor.doc_id and p.page_id not in seen]
            if anchor.page_no is not None:
                neighbors.sort(key=lambda p:(abs((p.page_no or 0)-anchor.page_no),p.page_no or 0))
            else:
                neighbors.sort(key=lambda p:(p.page_no is None,p.page_no or 0))
            for page in neighbors:
                ordered.append(page); seen.add(page.page_id)
                if len(ordered)>=8: return ordered
        return ordered[:8]

    def _evidence_pages_for_verify(self, branch: str, hits: List[RetrievalHit]) -> List[Page]:
        """
        白话：Verifier 要检查「答案是不是胡编」，得给它**看哪几页当证据**。本方法决定「证据页」取哪些。

        - 先把 hits（检索结果）换成真正的 Page 对象。
        - fact（事实题）：证据范围要宽一点 → 走 _expand_pages_for_evidence（同文档多页 / Excel 多 sheet）。
        - multi（跨页）：最多 8 页 top 命中本身。
        - chart（图表）：最多 4 页。
        """
        pages = [self.retriever.get_page(h.page_id) for h in hits]
        if not pages:
            return []
        if branch == RouterAgent.BRANCH_FACT:
            return self._expand_pages_for_evidence(pages)
        if branch == RouterAgent.BRANCH_MULTI:
            return pages[: min(8, len(pages))]
        if branch == RouterAgent.BRANCH_CHART:
            return pages[: min(4, len(pages))]
        return self._expand_pages_for_evidence(pages)

    def _run_branch(self, branch: str, query: str, hits: List[RetrievalHit]) -> str:
        """
        白话：Router 已经告诉我们「这道题像哪种题」，这里**真正去调对应的答题函数**。

        根据分支名称调用对应工具，得到“候选答案”。

        注意：这里的 hits 是检索出来的 top-k 页面。不同分支用到的页面数量不同：
        - fact_qa：表格类会合并同一文件全部 sheet；其它类型合并多页命中
        - multi_page_qa：跨页聚合，取前若干页
        - chart_qa：图表 + 说明，取前 2 页

        副作用：会把本次涉及的文件名记到 `_last_source_files`，供 ask() 最后塞进返回结果。
        """
        pages = [self.retriever.get_page(h.page_id) for h in hits]

        if not pages:
            self._last_source_files = []
            return "没有检索到候选页面。"
        if branch == RouterAgent.BRANCH_FACT:
            ev = self._expand_pages_for_evidence(pages)
            self._last_source_files = self._source_file_names(ev)
            return fact_qa(query, ev, self.router.llm_client)
        if branch == RouterAgent.BRANCH_MULTI:
            sub = pages[: min(6, len(pages))]
            self._last_source_files = self._source_file_names(sub)
            return multi_page_qa(query, sub, self.router.llm_client)
        if branch == RouterAgent.BRANCH_CHART:
            sub = pages[:2]
            self._last_source_files = self._source_file_names(sub)
            return chart_qa(query, sub)
        ev = self._expand_pages_for_evidence(pages)
        self._last_source_files = self._source_file_names(ev)
        return fact_qa(query, ev, self.router.llm_client)

    @staticmethod
    def _resolve_initial_topk(branch: str, requested_topk: int) -> int:
        if requested_topk != SETTINGS.topk_default:
            return requested_topk
        if branch == RouterAgent.BRANCH_MULTI:
            return SETTINGS.topk_multi_page
        if branch == RouterAgent.BRANCH_CHART:
            return SETTINGS.topk_chart
        return SETTINGS.topk_fact

    @staticmethod
    def _resolve_retry_topk(current_topk: int) -> int:
        return min(current_topk * SETTINGS.topk_retry_multiplier, SETTINGS.max_retry_topk)

    def ask(
        self,
        query: str,
        topk: int = SETTINGS.topk_default,
        session_id: str = "default",
        forced_branch: str = "",
        event_callback: Optional[Callable[[StageTrace], None]] = None,
    ) -> QAResult:
        """
        对外主入口（白话：**用户问一句话，本方法跑完一整条流水线并打包返回**）。

        返回完整 QAResult，方便：
        - 打印日志
        - 离线复盘
        - 面试时展示系统可解释性

        你读这段代码时，建议按下面顺序理解（非常像后端服务处理请求）：
        1) 先召回候选页面 hits（相当于“先查库拿候选数据”）
        2) 再决定走哪个分支 branch（相当于“选择业务策略/处理器”）
        3) 执行工具得到 answer（相当于“调用下游服务”）
        4) verifier 校验 answer 是否可信（相当于“业务校验/风控”）
        5) 失败则 fallback：扩 top-k 重试一次（相当于“降级/重试策略”）
        """
        stage_traces: List[StageTrace] = []
        def record_stage(stage: StageTrace) -> None:
            stage_traces.append(stage)
            if event_callback:
                event_callback(stage)
        retry_reason = ""
        route_branch = RouterAgent.BRANCH_FACT

        # --- 前置拦截：闲聊/自我指代类问题不走知识库 ---
        if self._is_smalltalk_query(query):
            result = QAResult(
                query=query,
                rewritten_query=query,
                branch=RouterAgent.BRANCH_FACT,
                answer=self._fallback_smalltalk_answer(),
                verified=False,
                hits=[],
                retry_hits=None,
                source_files=[],
                citation_details=[],
                trace=AgentTrace(
                    route_branch=RouterAgent.BRANCH_FACT,
                    fallback_triggered=False,
                    retry_reason="smalltalk_blocked",
                    stages=[],
                ),
            )
            self.memory.add_record(result, session_id=session_id)
            return result

        if SETTINGS.enable_session_cache and not forced_branch:
            cached = self.memory.try_get(session_id, query)
            if cached is not None:
                if event_callback:
                    event_callback(StageTrace(stage="cache_hit",elapsed_ms=0,detail={"message":"命中会话缓存"}))
                return cached

        # --- 第 0 步：路由（用于分支自适应 top-k）---
        t0 = time.perf_counter()
        allowed_branches={RouterAgent.BRANCH_FACT,RouterAgent.BRANCH_MULTI,RouterAgent.BRANCH_CHART}
        if forced_branch and forced_branch not in allowed_branches:
            raise ValueError(f"unsupported forced branch: {forced_branch}")
        route_branch = forced_branch or self.router.route(query)
        route_cost = int((time.perf_counter() - t0) * 1000)
        record_stage(
            StageTrace(stage="route", elapsed_ms=route_cost, detail={"branch": route_branch})
        )
        effective_topk = self._resolve_initial_topk(route_branch, topk)

        # --- 第 1 步：检索（白话：去向量库按相似度拿回 top-k 条「哪一页」）---
        t1 = time.perf_counter()
        rewritten_query, hits = self.retriever.retrieve(query=query, topk=effective_topk)
        retrieval_cost = int((time.perf_counter() - t1) * 1000)
        record_stage(
            StageTrace(
                stage="retrieve",
                elapsed_ms=retrieval_cost,
                detail={
                    "topk": str(effective_topk),
                    "hit_count": str(len(hits)),
                    **self.retriever.last_diagnostics,
                },
            )
        )

        low_conf_retry_hits = None
        low_conf_retry_query = ""
        if self._is_low_confidence(hits) and SETTINGS.enable_agentic_retry_refine:
            low_conf_answer = "材料中未找到足够依据来回答该问题。"
            low_conf_evidence = self._evidence_pages_for_verify(route_branch, hits)
            critique = self._critique_retrieval(
                query=query,
                branch=route_branch,
                answer=low_conf_answer,
                hits=hits,
                evidence_pages=low_conf_evidence,
            )
            retry_query = self._refined_retry_query(query=query, branch=route_branch, critique=critique)
            low_conf_retry_query = retry_query
            record_stage(
                StageTrace(
                    stage="agentic_critique",
                    elapsed_ms=0,
                    detail={**critique, "retry_query": retry_query},
                )
            )
            retry_topk = self._resolve_retry_topk(effective_topk)
            tr = time.perf_counter()
            retry_rewritten_query, retry_hits = self.retriever.retrieve(query=retry_query, topk=retry_topk)
            low_conf_retry_hits = retry_hits
            record_stage(
                StageTrace(
                    stage="retry_retrieve",
                    elapsed_ms=int((time.perf_counter() - tr) * 1000),
                    detail={
                        "retry_topk": str(retry_topk),
                        "retry_hit_count": str(len(retry_hits)),
                        "retry_query": retry_query,
                        **self.retriever.last_diagnostics,
                    },
                )
            )
            if not self._is_low_confidence(retry_hits):
                hits = retry_hits
                rewritten_query = retry_rewritten_query
                effective_topk = retry_topk

        if self._is_low_confidence(hits):
            answer = (
                "材料中未找到足够依据来回答该问题。"
                "请补充更具体的关键词（文档名、字段名、时间、模块名）后再试。"
            )
            if low_conf_retry_query:
                answer += f" 系统已尝试改写检索：{low_conf_retry_query}，仍未找到可靠证据。"
            result = QAResult(
                query=query,
                rewritten_query=rewritten_query,
                branch=RouterAgent.BRANCH_FACT,
                answer=answer,
                verified=False,
                hits=hits,
                retry_hits=low_conf_retry_hits,
                source_files=[],
                citation_details=self._citation_details(low_conf_retry_hits or hits),
                trace=AgentTrace(
                    route_branch=route_branch,
                    fallback_triggered=False,
                    retry_reason="low_confidence_retrieval",
                    stages=stage_traces,
                ),
            )
            self.memory.add_record(result, session_id=session_id)
            return result

        branch = route_branch

        # --- 第 3 步：生成（白话：按上一步的分支，调用 tools 里对应函数写出答案草稿）---
        t2 = time.perf_counter()
        self._last_source_files = []
        answer = self._run_branch(branch=branch, query=query, hits=hits)
        source_files = list(self._last_source_files)
        generation_cost = int((time.perf_counter() - t2) * 1000)
        record_stage(
            StageTrace(
                stage="generate",
                elapsed_ms=generation_cost,
                detail={"branch": branch, "source_files": str(len(source_files))},
            )
        )

        # --- 第 4 步：校验（白话：Verifier 对照「证据页」看答案靠不靠谱；evidence 可能比 top-k 宽）---
        t3 = time.perf_counter()
        evidence_pages = self._evidence_pages_for_verify(branch, hits)
        verified = self.verifier.verify(query=query,answer=answer, pages=evidence_pages)
        verify_cost = int((time.perf_counter() - t3) * 1000)
        record_stage(
            StageTrace(
                stage="verify",
                elapsed_ms=verify_cost,
                detail={"verified": str(verified).lower(), "evidence_pages": str(len(evidence_pages))},
            )
        )

        # --- 第 5 步：内层重试（白话：验不过 → 检索多捞几页 → 同一题型再答一遍 → 再验；只做这一轮）---
        retry_hits = None
        fallback_triggered = False
        if not verified:
            retry_reason = "verifier_failed"
            retry_topk = self._resolve_retry_topk(effective_topk)
            retry_branch = branch
            critique = self._critique_retrieval(
                query=query,
                branch=branch,
                answer=answer,
                hits=hits,
                evidence_pages=evidence_pages,
            )
            retry_query = (
                self._refined_retry_query(query=query, branch=branch, critique=critique)
                if SETTINGS.enable_agentic_retry_refine
                else query
            )
            record_stage(
                StageTrace(
                    stage="agentic_critique",
                    elapsed_ms=0,
                    detail={**critique, "retry_query": retry_query},
                )
            )
            if SETTINGS.enable_branch_fallback:
                retry_branch = self.router.fallback_branch(branch)
                if retry_branch != branch:
                    fallback_triggered = True
                    retry_reason = "verifier_failed_with_branch_fallback"
            tr = time.perf_counter()
            _, retry_hits = self.retriever.retrieve(query=retry_query, topk=retry_topk)
            record_stage(
                StageTrace(
                    stage="retry_retrieve",
                    elapsed_ms=int((time.perf_counter() - tr) * 1000),
                    detail={
                        "retry_topk": str(retry_topk),
                        "retry_hit_count": str(len(retry_hits)),
                        "retry_query": retry_query,
                        **self.retriever.last_diagnostics,
                    },
                )
            )

            tg = time.perf_counter()
            answer = self._run_branch(branch=retry_branch, query=query, hits=retry_hits)
            source_files = list(self._last_source_files)
            record_stage(
                StageTrace(
                    stage="retry_generate",
                    elapsed_ms=int((time.perf_counter() - tg) * 1000),
                    detail={"branch": retry_branch, "source_files": str(len(source_files))},
                )
            )

            tv = time.perf_counter()
            retry_evidence = self._evidence_pages_for_verify(retry_branch, retry_hits)
            verified = self.verifier.verify(query=query,answer=answer, pages=retry_evidence)
            record_stage(
                StageTrace(
                    stage="retry_verify",
                    elapsed_ms=int((time.perf_counter() - tv) * 1000),
                    detail={"verified": str(verified).lower(), "evidence_pages": str(len(retry_evidence))},
                )
            )
            branch = retry_branch

        # --- 第 6 步：装盒返回（白话：把问句、改写句、分支、答案、是否验过、两次检索命中等塞进 QAResult）---
        result = QAResult(
            query=query,
            rewritten_query=rewritten_query,
            branch=branch,
            answer=answer,
            verified=verified,
            hits=hits,
            retry_hits=retry_hits,
            source_files=source_files,
            citation_details=self._citation_details(retry_hits or hits),
            trace=AgentTrace(
                route_branch=route_branch,
                fallback_triggered=fallback_triggered,
                retry_reason=retry_reason,
                stages=stage_traces,
            ),
        )

        # --- 第 7 步：记会话（白话：把这次问答记进内存/Redis，以后追问可能省检索；不影响本次答案）---
        self.memory.add_record(result, session_id=session_id)
        return result
