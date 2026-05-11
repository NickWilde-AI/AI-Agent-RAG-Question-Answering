"""
端到端 Agent Pipeline。

主流程：
1) 检索 top-k 页面
2) Router 选择分支
3) 执行工具
4) Verifier 校验
5) 不通过则扩召回重试
6) 写入 Session Memory

你可以把它当成 Java 后端里的“应用服务层 / 编排层”：
- 不做具体业务能力（那是 tools.py）
- 不做底层存储（那是 retriever.py）
- 只负责把各个组件按正确顺序串起来，并处理失败分支（verifier + fallback）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .config import SETTINGS
from .memory import SessionMemory
from .models import Page, QAResult, RetrievalHit
from .retriever import PageRetriever
from .router import RouterAgent
from .tools import chart_qa, fact_qa, multi_page_qa, translate_qa
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
    """

    retriever: PageRetriever
    router: RouterAgent
    memory: SessionMemory
    verifier: Verifier
    _last_source_files: List[str] = field(default_factory=list, init=False, repr=False)

    def _source_file_names(self, pages: List[Page]) -> List[str]:
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

    def _expand_pages_for_evidence(self, hit_pages: List[Page]) -> List[Page]:
        """Excel/CSV：同一工作簿全部 sheet；其它：只展开与「首要命中」同一 doc 的多页（不把 top-k 里其它文件混进材料）。"""
        if not hit_pages:
            return []
        p0 = hit_pages[0]
        ext = Path(p0.source_file).suffix.lower() if p0.source_file else ""
        merged = [p for p in self.retriever.pages if p.doc_id == p0.doc_id]
        merged.sort(key=lambda x: (x.page_no is None, x.page_no or 0))
        if ext in {".xlsx", ".xls", ".csv"}:
            return merged[:80]
        hit_ids = {hp.page_id for hp in hit_pages}
        front = [p for p in merged if p.page_id in hit_ids]
        rest = [p for p in merged if p.page_id not in hit_ids]
        ordered = front + rest
        return ordered[:8]

    def _evidence_pages_for_verify(self, branch: str, hits: List[RetrievalHit]) -> List[Page]:
        pages = [self.retriever.get_page(h.page_id) for h in hits]
        if not pages:
            return []
        if branch == RouterAgent.BRANCH_FACT:
            return self._expand_pages_for_evidence(pages)
        if branch == RouterAgent.BRANCH_MULTI:
            return pages[: min(8, len(pages))]
        if branch == RouterAgent.BRANCH_CHART:
            return pages[: min(4, len(pages))]
        if branch == RouterAgent.BRANCH_TRANSLATE:
            return pages[:1]
        return self._expand_pages_for_evidence(pages)

    def _run_branch(self, branch: str, query: str, hits: List[RetrievalHit]) -> str:
        """
        根据分支名称调用对应工具，得到“候选答案”。

        注意：这里的 hits 是检索出来的 top-k 页面。不同分支用到的页面数量不同：
        - fact_qa：表格类会合并同一文件全部 sheet；其它类型合并多页命中
        - multi_page_qa：跨页聚合，取前若干页
        - chart_qa：图表 + 说明，取前 2 页
        - translate_qa：一般单页翻译即可（pages[0]）
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
        if branch == RouterAgent.BRANCH_TRANSLATE:
            self._last_source_files = self._source_file_names(pages[:1])
            return translate_qa(query, pages[0], self.router.llm_client)
        ev = self._expand_pages_for_evidence(pages)
        self._last_source_files = self._source_file_names(ev)
        return fact_qa(query, ev, self.router.llm_client)

    def ask(self, query: str, topk: int = SETTINGS.topk_default) -> QAResult:
        """
        对外主入口。

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
        # 1) L0 检索：把 query 召回成 top-k 页面
        rewritten_query, hits = self.retriever.retrieve(query=query, topk=topk)

        # 2) L1 路由：判断这个问题属于哪一类（事实/跨页/图表/翻译）
        branch = self.router.route(query)

        # 3) L2 工具：按分支调用对应工具链，得到“候选答案”
        self._last_source_files = []
        answer = self._run_branch(branch=branch, query=query, hits=hits)
        source_files = list(self._last_source_files)

        # 4) Verifier：判断候选答案是否能在命中的页面中找到证据（表格校验时合并多 sheet）
        evidence_pages = self._evidence_pages_for_verify(branch, hits)
        verified = self.verifier.verify(answer=answer, pages=evidence_pages)

        retry_hits = None
        if not verified:
            # 5) fallback：如果校验失败，扩 top-k 再召回一次（扩大证据范围）
            retry_topk = topk * SETTINGS.topk_retry_multiplier
            _, retry_hits = self.retriever.retrieve(query=query, topk=retry_topk)
            answer = self._run_branch(branch=branch, query=query, hits=retry_hits)
            source_files = list(self._last_source_files)
            retry_evidence = self._evidence_pages_for_verify(branch, retry_hits)
            verified = self.verifier.verify(answer=answer, pages=retry_evidence)

        # 6) 封装运行轨迹：把“改写后的 query、分支、命中页、是否重试”等全部记录下来
        result = QAResult(
            query=query,
            rewritten_query=rewritten_query,
            branch=branch,
            answer=answer,
            verified=verified,
            hits=hits,
            retry_hits=retry_hits,
            source_files=source_files,
        )

        # 7) 写入会话缓存：真实系统中这里通常是 Redis 或持久化日志，用于复盘/减少重复调用
        self.memory.add_record(result)
        return result
