from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


SkillLifecycleStatus = Literal["implemented", "partial", "mock", "planned"]
SkillRunStatus = Literal["success", "failed", "need_more_info", "unsupported"]


class SkillSpec(BaseModel):
    name: str
    display_name: str
    description: str
    category: str
    status: SkillLifecycleStatus
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "medium"
    capabilities: List[str] = Field(default_factory=list)
    example_queries: List[str] = Field(default_factory=list)


class SkillResult(BaseModel):
    skill_name: str
    status: SkillRunStatus
    answer: str
    structured_data: Dict[str, Any] = Field(default_factory=dict)
    evidence_pages: List[Dict[str, Any]] = Field(default_factory=list)
    trace: Optional[Dict[str, Any]] = None
    warnings: List[str] = Field(default_factory=list)
    next_actions: List[str] = Field(default_factory=list)


class AgentCenterRunRequest(BaseModel):
    skill_name: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    workspace_id: Optional[str] = None
    top_k: int = Field(default=3, ge=1, le=20)
    session_id: str = Field(default="agent-center", min_length=1, max_length=128)
    options: Dict[str, Any] = Field(default_factory=dict)
