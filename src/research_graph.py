"""Planner / Executor / Verifier 多角色研究工作流；角色只编排，不复制 RAG 业务实现。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph


class ResearchGraphState(TypedDict, total=False):
    objective: str
    documents: List[Dict[str, Any]]
    steps: List[Any]
    findings: List[Dict[str, Any]]
    verified_findings: List[Dict[str, Any]]
    role_trace: List[str]


class ResearchAgentWorkflow:
    """显式多角色图。每个节点只有一项职责，可单独观测或替换。"""

    def __init__(
        self,
        planner: Callable[[str, List[Dict[str, Any]]], List[Any]],
        executor: Callable[[List[Any]], List[Dict[str, Any]]],
        verifier: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]] | None = None,
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.verifier = verifier or self._evidence_verifier
        graph = StateGraph(ResearchGraphState)
        graph.add_node("planner_agent", self._plan)
        graph.add_node("executor_agent", self._execute)
        graph.add_node("verifier_agent", self._verify)
        graph.add_edge(START, "planner_agent")
        graph.add_edge("planner_agent", "executor_agent")
        graph.add_edge("executor_agent", "verifier_agent")
        graph.add_edge("verifier_agent", END)
        self.graph = graph.compile()

    def _plan(self, state: ResearchGraphState) -> ResearchGraphState:
        return {"steps": self.planner(state["objective"], state.get("documents", [])), "role_trace": ["planner_agent"]}

    def _execute(self, state: ResearchGraphState) -> ResearchGraphState:
        return {"findings": self.executor(state.get("steps", [])), "role_trace": [*state.get("role_trace", []), "executor_agent"]}

    def _verify(self, state: ResearchGraphState) -> ResearchGraphState:
        return {"verified_findings": self.verifier(state.get("findings", [])), "role_trace": [*state.get("role_trace", []), "verifier_agent"]}

    @staticmethod
    def _evidence_verifier(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [x for x in findings if x.get("verified") and x.get("evidence")]

    def run(self, objective: str, documents: List[Dict[str, Any]]) -> ResearchGraphState:
        return self.graph.invoke({"objective": objective, "documents": documents, "role_trace": []})
