from __future__ import annotations

from typing import Any, Callable, Optional

from .schemas import AgentCenterRunRequest, SkillResult, SkillSpec
from .skill_registry import SkillExecutionContext, SkillRegistry
from .skills import FormInvoiceSkill, HRRecruitingSkill, RAGSkill, ReportAnalysisSkill


def build_default_skill_registry() -> SkillRegistry:
    return SkillRegistry(
        [
            RAGSkill(),
            ReportAnalysisSkill(),
            FormInvoiceSkill(),
            HRRecruitingSkill(),
        ]
    )


class AgentCenterRuntime:
    def __init__(
        self,
        get_default_engine: Callable[[], Any],
        get_research_executor: Callable[[], Any],
        registry: Optional[SkillRegistry] = None,
    ) -> None:
        self._get_default_engine = get_default_engine
        self._get_research_executor = get_research_executor
        self._registry = registry or build_default_skill_registry()

    def list_skills(self) -> list[SkillSpec]:
        return self._registry.list_specs()

    def get_skill(self, skill_name: str) -> Optional[SkillSpec]:
        skill = self._registry.get(skill_name)
        return skill.spec if skill else None

    def _resolve_engine(self, workspace_id: Optional[str]) -> Any:
        if workspace_id:
            return self._get_research_executor().build_workspace_engine(workspace_id)
        return self._get_default_engine()

    def run(self, request: AgentCenterRunRequest) -> SkillResult:
        skill = self._registry.get(request.skill_name)
        if skill is None:
            return SkillResult(
                skill_name=request.skill_name,
                status="unsupported",
                answer=f"未知 skill: {request.skill_name}",
                warnings=["请先通过 /agent-center/skills 查看可用 skill 列表。"],
            )
        try:
            engine = self._resolve_engine(request.workspace_id)
            context = SkillExecutionContext(
                engine=engine,
                workspace_id=request.workspace_id,
                top_k=request.top_k,
                session_id=request.session_id,
                options=request.options,
            )
            return skill.run(request.query, context)
        except Exception as exc:
            return SkillResult(
                skill_name=request.skill_name,
                status="failed",
                answer=f"{skill.spec.display_name} 执行失败：{exc}",
                warnings=["该结果属于运行失败，请结合 trace、workspace 和文档状态继续排查。"],
                next_actions=["确认 workspace 是否存在可用文档，或先调用 /agent-center/skills 查看 skill 状态。"],
            )
