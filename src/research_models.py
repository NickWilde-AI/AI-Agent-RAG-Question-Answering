"""企业研究任务领域对象。与即时问答模型分离，但不复制 RAG 链路。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass
class Evidence:
    doc_id: str
    file_name: str
    page_id: str
    page_no: Optional[int]
    score: float
    excerpt: str
    source_type: str = "document"


@dataclass
class ResearchStep:
    step_id: str
    title: str
    description: str
    tool_name: str
    query: str
    status: str = "pending"
    answer: str = ""
    verified: bool = False
    evidence: List[Evidence] = field(default_factory=list)
    trace: List[Dict[str, Any]] = field(default_factory=list)
    error_message: str = ""


@dataclass
class ResearchJob:
    job_id: str
    workspace_id: str
    session_id: str
    objective: str
    status: str = "pending"
    progress: int = 0
    current_step: str = ""
    plan: List[ResearchStep] = field(default_factory=list)
    findings: List[Dict[str, Any]] = field(default_factory=list)
    report_id: Optional[str] = None
    error_message: str = ""
    created_at: str = field(default_factory=utc_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    idempotency_key: Optional[str] = None


@dataclass
class ResearchReport:
    report_id: str
    job_id: str
    title: str
    summary: str
    markdown_content: str
    html_content: str
    citations: List[Evidence]
    created_at: str = field(default_factory=utc_now)


def to_dict(value: Any) -> Dict[str, Any]:
    return asdict(value)


JOB_TRANSITIONS = {
    "pending": {"planning", "cancelled", "failed"}, "planning": {"running", "cancelled", "failed"},
    "running": {"verifying", "cancelled", "failed"}, "verifying": {"completed", "failed", "cancelled"},
    "completed": set(), "failed": set(), "cancelled": set(),
}


def validate_job_transition(current: str, target: str) -> None:
    if target not in JOB_TRANSITIONS.get(current, set()):
        raise ValueError(f"illegal research job transition: {current} -> {target}")
