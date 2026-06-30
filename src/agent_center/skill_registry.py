from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Protocol

from .schemas import SkillResult, SkillSpec


@dataclass
class SkillExecutionContext:
    engine: Any
    workspace_id: Optional[str]
    top_k: int
    session_id: str
    options: Dict[str, Any]


class AgentSkill(Protocol):
    spec: SkillSpec

    def run(self, query: str, context: SkillExecutionContext) -> SkillResult:
        ...


class SkillRegistry:
    def __init__(self, skills: Iterable[AgentSkill]) -> None:
        self._skills = {skill.spec.name: skill for skill in skills}

    def list_specs(self) -> List[SkillSpec]:
        return [self._skills[name].spec for name in sorted(self._skills)]

    def get(self, name: str) -> Optional[AgentSkill]:
        return self._skills.get(name)

    def require(self, name: str) -> AgentSkill:
        skill = self.get(name)
        if skill is None:
            raise KeyError(name)
        return skill
