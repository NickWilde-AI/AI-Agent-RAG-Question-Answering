"""
pipeline.py — 端到端编排（QAEngine）：简历里「Agent 主链路」的心脏

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
`QAEngine.ask` 把下面步骤**顺序固定**（和面试讲稿一一对应）：
1) L0 **检索**：`retriever.retrieve` → `rewritten_query` + `hits`
2) L1 **路由**：`router.route` → `branch`（fact / multi / chart / translate）
3) L2 **生成**：`_run_branch` → 调 `tools.*_qa` 得到 `answer`
4) **校验**：`verifier.verify` → `verified`
5) **重试（扩召回）**：若未通过，`topk * multiplier` 再 retrieve + 再生成 + 再 verify
6) **会话记忆**：`memory.add_record`

注意：`agent_loop.py` 是**外层**再包一层「整次 ask 仍不 verified 则加大 topk 再来」；本文件内部还有一次「verifier 失败扩 top-k」。

================================================================================
【类比 Android】
================================================================================
- `QAEngine` ≈ **ViewModel / UseCaseInteractor**：持有多个 Repository（Retriever、Router、Tools 通过函数注入）、调用顺序清晰。
- `_run_branch` ≈ `when(branch)` 分发到不同 **Handler**（fact_qa 等），像多类型 UI 事件走不同 Presenter。
- `_expand_pages_for_evidence`：Excel 多 sheet 合并进上下文 ≈ 把「同一 Document 多 Fragment」拼成一份材料再给模型。


主流程（速记）：
1) 检索 top-k 页面 → 2) Router → 3) 工具生成 → 4) Verifier → 5) 失败扩召回 → 6) Memory

你可以把它当成 Java 后端里的“应用服务层 / 编排层”：
- 不做具体业务能力（那是 tools.py）
- 不做底层存储（那是 retriever.py）
- 只负责把各个组件按正确顺序串起来，并处理失败分支（verifier + fallback）
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

    def _expand_pages_for_evidence(self, hit_pages: List[Page]) -> List[Page]:
        """
        白话：检索只命中了几页，但「答题 / 校验」有时需要**同一文件里更多页**一起看。

        - 先看「排第一的那条命中」属于哪个文档（doc_id）、什么扩展名。
        - 从全库 `retriever.pages` 里捞出**同一 doc_id** 的所有页，按页码排序。
        - Excel/CSV：表格题常要跨 sheet，最多取 80 页一起当材料。
        - 普通 PDF 等：命中页放前面，同文档其余页放后面，总共最多 8 页（控制长度）。
        """
        if not hit_pages:
            return []
        p0 = hit_pages[0]  # 「主命中」：代表用户最可能想问的那份文档
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
        """
        白话：Verifier 要检查「答案是不是胡编」，得给它**看哪几页当证据**。本方法决定「证据页」取哪些。

        - 先把 hits（检索结果）换成真正的 Page 对象。
        - fact（事实题）：证据范围要宽一点 → 走 _expand_pages_for_evidence（同文档多页 / Excel 多 sheet）。
        - multi（跨页）：最多 8 页 top 命中本身。
        - chart（图表）：最多 4 页。
        - translate（翻译）：一般只看 1 页外文材料。
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
        if branch == RouterAgent.BRANCH_TRANSLATE:
            return pages[:1]
        return self._expand_pages_for_evidence(pages)

    def _run_branch(self, branch: str, query: str, hits: List[RetrievalHit]) -> str:
        """
        白话：Router 已经告诉我们「这道题像哪种题」，这里**真正去调对应的答题函数**。

        根据分支名称调用对应工具，得到“候选答案”。

        注意：这里的 hits 是检索出来的 top-k 页面。不同分支用到的页面数量不同：
        - fact_qa：表格类会合并同一文件全部 sheet；其它类型合并多页命中
        - multi_page_qa：跨页聚合，取前若干页
        - chart_qa：图表 + 说明，取前 2 页
        - translate_qa：一般单页翻译即可（pages[0]）

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
        if branch == RouterAgent.BRANCH_TRANSLATE:
            self._last_source_files = self._source_file_names(pages[:1])
            return translate_qa(query, pages[0], self.router.llm_client)
        ev = self._expand_pages_for_evidence(pages)
        self._last_source_files = self._source_file_names(ev)
        return fact_qa(query, ev, self.router.llm_client)

    def ask(self, query: str, topk: int = SETTINGS.topk_default) -> QAResult:
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
        # --- 第 1 步：检索（白话：去向量库按相似度拿回 top-k 条「哪一页」）---
        rewritten_query, hits = self.retriever.retrieve(query=query, topk=topk)

        # --- 第 2 步：路由（白话：判断这道题更像单页事实 / 多页 / 图表 / 翻译里的哪一种）---
        branch = self.router.route(query)

        # --- 第 3 步：生成（白话：按上一步的分支，调用 tools 里对应函数写出答案草稿）---
        self._last_source_files = []
        answer = self._run_branch(branch=branch, query=query, hits=hits)
        source_files = list(self._last_source_files)

        # --- 第 4 步：校验（白话：Verifier 对照「证据页」看答案靠不靠谱；evidence 可能比 top-k 宽）---
        evidence_pages = self._evidence_pages_for_verify(branch, hits)
        verified = self.verifier.verify(answer=answer, pages=evidence_pages)

        # --- 第 5 步：内层重试（白话：验不过 → 检索多捞几页 → 同一题型再答一遍 → 再验；只做这一轮）---
        retry_hits = None
        if not verified:
            retry_topk = topk * SETTINGS.topk_retry_multiplier
            _, retry_hits = self.retriever.retrieve(query=query, topk=retry_topk)
            answer = self._run_branch(branch=branch, query=query, hits=retry_hits)
            source_files = list(self._last_source_files)
            retry_evidence = self._evidence_pages_for_verify(branch, retry_hits)
            verified = self.verifier.verify(answer=answer, pages=retry_evidence)

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
        )

        # --- 第 7 步：记会话（白话：把这次问答记进内存/Redis，以后追问可能省检索；不影响本次答案）---
        self.memory.add_record(result)
        return result
