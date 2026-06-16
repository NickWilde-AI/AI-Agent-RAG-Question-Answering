"""
langgraph_engine.py - 基于 LangGraph 的 QA 编排实现。

目标：
- 在不改业务行为的前提下，将现有 QAEngine.ask 的流程映射为图编排。
- 通过配置开关启用，便于与自研状态机做 A/B。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .config import SETTINGS
from .memory import SessionMemory
from .models import AgentTrace, QAResult, RetrievalHit, StageTrace
from .pipeline import QAEngine
from .retriever import PageRetriever
from .router import RouterAgent
from .verifier import Verifier


class QAState(TypedDict, total=False):
    query: str
    session_id: str
    requested_topk: int
    route_branch: str
    branch: str
    effective_topk: int
    rewritten_query: str
    hits: List[RetrievalHit]
    answer: str
    verified: bool
    retry_hits: Optional[List[RetrievalHit]]
    retry_query: str
    fallback_triggered: bool
    retry_reason: str
    source_files: List[str]
    stage_traces: List[StageTrace]
    cache_result: Optional[QAResult]
    should_finish: bool
    skip_memory_write: bool
    low_confidence: bool


@dataclass
class LangGraphQAEngine:
    """
    LangGraph 版编排器。

    说明：
    - 使用 QAEngine 的已有能力（小聊拦截、分支工具、证据页策略）避免行为漂移。
    - 输出保持 QAResult 结构一致。
    """

    retriever: PageRetriever
    router: RouterAgent
    memory: SessionMemory
    verifier: Verifier

    def __post_init__(self) -> None:
        self._base = QAEngine(
            retriever=self.retriever,
            router=self.router,
            memory=self.memory,
            verifier=self.verifier,
        )
        self._graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(QAState)
        graph.add_node("smalltalk_gate", self._smalltalk_gate)
        graph.add_node("cache_lookup", self._cache_lookup)
        graph.add_node("route", self._route)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("generate", self._generate)
        graph.add_node("verify", self._verify)
        graph.add_node("retry_prepare", self._retry_prepare)
        graph.add_node("retry_retrieve", self._retry_retrieve)
        graph.add_node("retry_generate", self._retry_generate)
        graph.add_node("retry_verify", self._retry_verify)

        graph.add_edge(START, "smalltalk_gate")
        graph.add_conditional_edges(
            "smalltalk_gate",
            lambda s: "end" if s.get("should_finish") else "cache_lookup",
            {"end": END, "cache_lookup": "cache_lookup"},
        )
        graph.add_conditional_edges(
            "cache_lookup",
            lambda s: "end" if s.get("should_finish") else "route",
            {"end": END, "route": "route"},
        )
        graph.add_edge("route", "retrieve")
        graph.add_conditional_edges(
            "retrieve",
            lambda s: "end" if s.get("should_finish") else "generate",
            {"end": END, "generate": "generate"},
        )
        graph.add_edge("generate", "verify")
        graph.add_conditional_edges(
            "verify",
            lambda s: "end" if s.get("verified") else "retry_prepare",
            {"end": END, "retry_prepare": "retry_prepare"},
        )
        graph.add_edge("retry_prepare", "retry_retrieve")
        graph.add_edge("retry_retrieve", "retry_generate")
        graph.add_edge("retry_generate", "retry_verify")
        graph.add_edge("retry_verify", END)
        return graph.compile()

    def _smalltalk_gate(self, state: QAState) -> QAState:
        query = state["query"]
        if not self._base._is_smalltalk_query(query):
            return {"should_finish": False}
        result = QAResult(
            query=query,
            rewritten_query=query,
            branch=RouterAgent.BRANCH_FACT,
            answer=self._base._fallback_smalltalk_answer(),
            verified=False,
            hits=[],
            retry_hits=None,
            source_files=[],
            trace=AgentTrace(
                route_branch=RouterAgent.BRANCH_FACT,
                fallback_triggered=False,
                retry_reason="smalltalk_blocked",
                stages=[],
            ),
        )
        return {
            "cache_result": result,
            "should_finish": True,
            "skip_memory_write": False,
        }

    def _cache_lookup(self, state: QAState) -> QAState:
        if not SETTINGS.enable_session_cache:
            return {"should_finish": False}
        cached = self.memory.try_get(state["session_id"], state["query"])
        if cached is None:
            return {"should_finish": False}
        return {
            "cache_result": cached,
            "should_finish": True,
            "skip_memory_write": True,
        }

    def _route(self, state: QAState) -> QAState:
        import time

        t0 = time.perf_counter()
        route_branch = self.router.route(state["query"])
        route_cost = int((time.perf_counter() - t0) * 1000)
        traces = list(state.get("stage_traces", []))
        traces.append(StageTrace(stage="route", elapsed_ms=route_cost, detail={"branch": route_branch}))
        effective_topk = self._base._resolve_initial_topk(route_branch, state["requested_topk"])
        return {
            "route_branch": route_branch,
            "branch": route_branch,
            "effective_topk": effective_topk,
            "stage_traces": traces,
        }

    def _retrieve(self, state: QAState) -> QAState:
        import time

        t1 = time.perf_counter()
        rewritten_query, hits = self.retriever.retrieve(query=state["query"], topk=state["effective_topk"])
        retrieval_cost = int((time.perf_counter() - t1) * 1000)
        traces = list(state.get("stage_traces", []))
        traces.append(
            StageTrace(
                stage="retrieve",
                elapsed_ms=retrieval_cost,
                detail={
                    "topk": str(state["effective_topk"]),
                    "hit_count": str(len(hits)),
                    **self.retriever.last_diagnostics,
                },
            )
        )
        low_conf = self._base._is_low_confidence(hits)
        if low_conf and SETTINGS.enable_agentic_retry_refine:
            low_conf_answer = "材料中未找到足够依据来回答该问题。"
            low_conf_evidence = self._base._evidence_pages_for_verify(state["route_branch"], hits)
            critique = self._base._critique_retrieval(
                query=state["query"],
                branch=state["route_branch"],
                answer=low_conf_answer,
                hits=hits,
                evidence_pages=low_conf_evidence,
            )
            retry_query = self._base._refined_retry_query(
                query=state["query"],
                branch=state["route_branch"],
                critique=critique,
            )
            traces.append(
                StageTrace(
                    stage="agentic_critique",
                    elapsed_ms=0,
                    detail={**critique, "retry_query": retry_query},
                )
            )
            retry_topk = self._base._resolve_retry_topk(state["effective_topk"])
            tr = time.perf_counter()
            retry_rewritten_query, retry_hits = self.retriever.retrieve(query=retry_query, topk=retry_topk)
            traces.append(
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
            if not self._base._is_low_confidence(retry_hits):
                rewritten_query = retry_rewritten_query
                hits = retry_hits
                low_conf = False
        if low_conf:
            answer = (
                "材料中未找到足够依据来回答该问题。"
                "请补充更具体的关键词（文档名、字段名、时间、模块名）后再试。"
            )
            result = QAResult(
                query=state["query"],
                rewritten_query=rewritten_query,
                branch=RouterAgent.BRANCH_FACT,
                answer=answer,
                verified=False,
                hits=hits,
                retry_hits=None,
                source_files=[],
                trace=AgentTrace(
                    route_branch=state["route_branch"],
                    fallback_triggered=False,
                    retry_reason="low_confidence_retrieval",
                    stages=traces,
                ),
            )
            return {
                "rewritten_query": rewritten_query,
                "hits": hits,
                "stage_traces": traces,
                "cache_result": result,
                "should_finish": True,
                "skip_memory_write": False,
            }
        return {
            "rewritten_query": rewritten_query,
            "hits": hits,
            "stage_traces": traces,
            "should_finish": False,
        }

    def _generate(self, state: QAState) -> QAState:
        import time

        t2 = time.perf_counter()
        self._base._last_source_files = []
        answer = self._base._run_branch(branch=state["branch"], query=state["query"], hits=state["hits"])
        source_files = list(self._base._last_source_files)
        generation_cost = int((time.perf_counter() - t2) * 1000)
        traces = list(state.get("stage_traces", []))
        traces.append(
            StageTrace(
                stage="generate",
                elapsed_ms=generation_cost,
                detail={"branch": state["branch"], "source_files": str(len(source_files))},
            )
        )
        return {"answer": answer, "source_files": source_files, "stage_traces": traces}

    def _verify(self, state: QAState) -> QAState:
        import time

        t3 = time.perf_counter()
        evidence_pages = self._base._evidence_pages_for_verify(state["branch"], state["hits"])
        verified = self.verifier.verify(answer=state["answer"], pages=evidence_pages)
        verify_cost = int((time.perf_counter() - t3) * 1000)
        traces = list(state.get("stage_traces", []))
        traces.append(
            StageTrace(
                stage="verify",
                elapsed_ms=verify_cost,
                detail={"verified": str(verified).lower(), "evidence_pages": str(len(evidence_pages))},
            )
        )
        return {"verified": verified, "stage_traces": traces}

    def _retry_prepare(self, state: QAState) -> QAState:
        retry_reason = "verifier_failed"
        retry_branch = state["branch"]
        fallback_triggered = False
        evidence_pages = self._base._evidence_pages_for_verify(state["branch"], state["hits"])
        critique = self._base._critique_retrieval(
            query=state["query"],
            branch=state["branch"],
            answer=state["answer"],
            hits=state["hits"],
            evidence_pages=evidence_pages,
        )
        retry_query = (
            self._base._refined_retry_query(query=state["query"], branch=state["branch"], critique=critique)
            if SETTINGS.enable_agentic_retry_refine
            else state["query"]
        )
        traces = list(state.get("stage_traces", []))
        traces.append(
            StageTrace(
                stage="agentic_critique",
                elapsed_ms=0,
                detail={**critique, "retry_query": retry_query},
            )
        )
        if SETTINGS.enable_branch_fallback:
            retry_branch = self.router.fallback_branch(state["branch"])
            if retry_branch != state["branch"]:
                fallback_triggered = True
                retry_reason = "verifier_failed_with_branch_fallback"
        retry_topk = self._base._resolve_retry_topk(state["effective_topk"])
        return {
            "retry_reason": retry_reason,
            "fallback_triggered": fallback_triggered,
            "branch": retry_branch,
            "effective_topk": retry_topk,
            "retry_query": retry_query,
            "stage_traces": traces,
        }

    def _retry_retrieve(self, state: QAState) -> QAState:
        import time

        tr = time.perf_counter()
        retry_query = state.get("retry_query") or state["query"]
        _, retry_hits = self.retriever.retrieve(query=retry_query, topk=state["effective_topk"])
        traces = list(state.get("stage_traces", []))
        traces.append(
            StageTrace(
                stage="retry_retrieve",
                elapsed_ms=int((time.perf_counter() - tr) * 1000),
                detail={
                    "retry_topk": str(state["effective_topk"]),
                    "retry_hit_count": str(len(retry_hits)),
                    "retry_query": retry_query,
                    **self.retriever.last_diagnostics,
                },
            )
        )
        return {"retry_hits": retry_hits, "stage_traces": traces}

    def _retry_generate(self, state: QAState) -> QAState:
        import time

        tg = time.perf_counter()
        answer = self._base._run_branch(branch=state["branch"], query=state["query"], hits=state["retry_hits"] or [])
        source_files = list(self._base._last_source_files)
        traces = list(state.get("stage_traces", []))
        traces.append(
            StageTrace(
                stage="retry_generate",
                elapsed_ms=int((time.perf_counter() - tg) * 1000),
                detail={"branch": state["branch"], "source_files": str(len(source_files))},
            )
        )
        return {"answer": answer, "source_files": source_files, "stage_traces": traces}

    def _retry_verify(self, state: QAState) -> QAState:
        import time

        tv = time.perf_counter()
        retry_hits = state.get("retry_hits") or []
        retry_evidence = self._base._evidence_pages_for_verify(state["branch"], retry_hits)
        verified = self.verifier.verify(answer=state["answer"], pages=retry_evidence)
        traces = list(state.get("stage_traces", []))
        traces.append(
            StageTrace(
                stage="retry_verify",
                elapsed_ms=int((time.perf_counter() - tv) * 1000),
                detail={"verified": str(verified).lower(), "evidence_pages": str(len(retry_evidence))},
            )
        )
        return {"verified": verified, "stage_traces": traces}

    def ask(self, query: str, topk: int = SETTINGS.topk_default, session_id: str = "default") -> QAResult:
        state: QAState = {
            "query": query,
            "session_id": session_id,
            "requested_topk": topk,
            "route_branch": RouterAgent.BRANCH_FACT,
            "branch": RouterAgent.BRANCH_FACT,
            "effective_topk": topk,
            "rewritten_query": query,
            "hits": [],
            "answer": "",
            "verified": False,
            "retry_hits": None,
            "retry_query": query,
            "fallback_triggered": False,
            "retry_reason": "",
            "source_files": [],
            "stage_traces": [],
            "cache_result": None,
            "should_finish": False,
            "skip_memory_write": False,
            "low_confidence": False,
        }
        out = self._graph.invoke(state)
        cached = out.get("cache_result")
        if cached is not None:
            if not out.get("skip_memory_write", False):
                self.memory.add_record(cached, session_id=session_id)
            return cached

        result = QAResult(
            query=query,
            rewritten_query=out.get("rewritten_query", query),
            branch=out.get("branch", RouterAgent.BRANCH_FACT),
            answer=out.get("answer", ""),
            verified=bool(out.get("verified", False)),
            hits=out.get("hits", []),
            retry_hits=out.get("retry_hits"),
            source_files=out.get("source_files", []),
            trace=AgentTrace(
                route_branch=out.get("route_branch", RouterAgent.BRANCH_FACT),
                fallback_triggered=bool(out.get("fallback_triggered", False)),
                retry_reason=out.get("retry_reason", ""),
                stages=out.get("stage_traces", []),
            ),
        )
        self.memory.add_record(result, session_id=session_id)
        return result
