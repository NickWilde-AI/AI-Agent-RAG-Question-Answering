"""Planner / Executor / Verifier 多角色研究工作流；角色只编排，不复制 RAG 业务实现。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, TypedDict

try:
    from langgraph.graph import END, START, StateGraph
except Exception:  # pragma: no cover - 本地依赖不兼容时使用轻量顺序图兜底
    END = "__end__"
    START = "__start__"

    class _CompiledGraph:
        def __init__(self, order: List[str], handlers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]) -> None:
            self._order = order
            self._handlers = handlers

        def invoke(self, state: Dict[str, Any]) -> Dict[str, Any]:
            current = dict(state)
            for name in self._order:
                update = self._handlers[name](current)
                if update:
                    current.update(update)
            return current

    class StateGraph:  # type: ignore[override]
        def __init__(self, *_args, **_kwargs) -> None:
            self._nodes: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
            self._edges: Dict[str, str] = {}

        def add_node(self, name: str, handler: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
            self._nodes[name] = handler

        def add_edge(self, left: str, right: str) -> None:
            self._edges[left] = right

        def compile(self) -> _CompiledGraph:
            order: List[str] = []
            cursor = self._edges.get(START)
            while cursor and cursor != END:
                order.append(cursor)
                cursor = self._edges.get(cursor)
            return _CompiledGraph(order, self._nodes)


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
