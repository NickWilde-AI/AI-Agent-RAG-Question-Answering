from __future__ import annotations

import re
from typing import Any, Dict, List

from ..schemas import SkillResult, SkillSpec
from ..skill_registry import SkillExecutionContext
from ..trace import build_evidence_pages, build_trace_payload


COMMON_SKILLS = [
    "Python", "FastAPI", "RAG", "Agent", "LLM", "LangGraph", "LangChain",
    "Redis", "Milvus", "Kafka", "Docker", "Kubernetes", "SQL", "PyTorch", "OCR", "VLM",
]

# 岗位 -> 期望技能及权重（权重越高越关键）。匹配分 = 命中权重和 / 目标权重和。
ROLE_SKILL_WEIGHTS = {
    "AI Agent": {"Python": 3, "RAG": 3, "Agent": 3, "LLM": 2, "FastAPI": 1, "Docker": 1},
    "算法": {"Python": 3, "PyTorch": 3, "LLM": 2, "VLM": 2},
    "后端": {"Python": 3, "FastAPI": 2, "Redis": 2, "SQL": 2, "Docker": 1},
}

# 合规红线：禁止据以做招聘判断的敏感属性关键词。
SENSITIVE_ATTRIBUTES = [
    "年龄", "岁", "性别", "男", "女", "民族", "婚育", "已婚", "未婚", "婚姻",
    "生育", "怀孕", "户籍", "籍贯", "宗教", "政治面貌", "残疾", "颜值", "长相",
]


def _joined_text_with_pages(engine: object, evidence_pages: List[Dict[str, object]]) -> List[Dict[str, str]]:
    chunks: List[Dict[str, str]] = []
    for item in evidence_pages:
        page = engine.retriever.get_page(item["page_id"])
        chunks.append({"page_id": item["page_id"], "content": getattr(page, "content", "") or ""})
    return chunks


def _role_target(query: str) -> str:
    for role in ROLE_SKILL_WEIGHTS:
        if role.lower() in query.lower():
            return role
    return "AI Agent"


def _skill_matrix(page_chunks: List[Dict[str, str]], target_role: str) -> List[Dict[str, Any]]:
    """对目标岗位的每个期望技能，给出是否命中、权重、证据页。"""
    weights = ROLE_SKILL_WEIGHTS[target_role]
    matrix: List[Dict[str, Any]] = []
    for skill, weight in weights.items():
        token = skill.upper()
        evidence_page = None
        for chunk in page_chunks:
            if token in chunk["content"].upper():
                evidence_page = chunk["page_id"]
                break
        matrix.append({
            "skill": skill,
            "weight": weight,
            "present": evidence_page is not None,
            "evidence_page": evidence_page,
        })
    return matrix


def _match_score(matrix: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = sum(row["weight"] for row in matrix)
    hit = sum(row["weight"] for row in matrix if row["present"])
    score = round(hit / total * 100, 1) if total else 0.0
    return {"score": score, "hit_weight": hit, "total_weight": total, "unit": "%"}


def _matched_skills(page_chunks: List[Dict[str, str]]) -> List[str]:
    full = "\n".join(c["content"] for c in page_chunks).upper()
    return [s for s in COMMON_SKILLS if s.upper() in full]


def _candidate_summary(page_chunks: List[Dict[str, str]], matched_skills: List[str]) -> str:
    text = "\n".join(c["content"] for c in page_chunks)
    years = re.findall(r"(\d+(?:\.\d+)?)\s*年", text)
    degree = re.search(r"(本科|硕士|博士)", text)
    parts: List[str] = []
    if matched_skills:
        parts.append("识别到的岗位相关技能包括 " + "、".join(matched_skills[:6]) + "。")
    if years:
        parts.append(f"材料中出现的相关经验年限线索为 {years[0]} 年。")
    if degree:
        parts.append(f"学历线索为 {degree.group(1)}。")
    if not parts:
        parts.append("当前材料中缺少标准化简历/JD 结构，已按通用文档内容给出辅助总结。")
    return " ".join(parts)


def _gaps_or_risks(matrix: List[Dict[str, Any]], matched_skills: List[str], has_jd: bool) -> List[str]:
    risks: List[str] = []
    missing = [row["skill"] for row in matrix if not row["present"]]
    if missing:
        risks.append("与目标岗位相比，当前材料未明显覆盖这些关键技能：" + "、".join(missing[:5]) + "。")
    if not has_jd:
        risks.append("当前上下文里未明确识别到岗位 JD，匹配判断偏向候选人画像总结。")
    if len(matched_skills) < 2:
        risks.append("岗位相关技能线索较少，建议补充更完整的简历或项目经历。")
    return risks


def _interview_questions(matched_skills: List[str], target_role: str) -> List[str]:
    seeds = matched_skills[:5] or list(ROLE_SKILL_WEIGHTS[target_role])[:5]
    questions = [f"请你结合最近一个项目，详细说明你如何使用 {skill} 解决过实际问题？" for skill in seeds]
    questions.extend([
        "请描述一次你做技术取舍的过程，最终为什么这么选？",
        "如果线上效果下降，你会怎样定位检索、生成和校验链路里的问题？",
        "你会如何设计一个可追溯、可评测的 AI Agent 服务？",
    ])
    return questions[:10]


def _detect_sensitive(query: str) -> List[str]:
    """检测问题是否要求基于敏感属性做判断。"""
    hits = []
    for attr in SENSITIVE_ATTRIBUTES:
        if attr in query and attr not in hits:
            hits.append(attr)
    return hits


class HRRecruitingSkill:
    spec = SkillSpec(
        name="hr_recruiting",
        display_name="HR 招聘辅助",
        description=(
            "复用多文档理解，输出候选人总结、岗位技能匹配矩阵与匹配分、面试问题；"
            "对涉及敏感属性的提问会合规拦截。逻辑已完备；生产前需扩充简历/JD 语料与合规评测集。"
        ),
        category="hr",
        status="partial",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "workspace_id": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "candidate_summary": {"type": "string"},
                "matched_skills": {"type": "array"},
                "skill_matrix": {"type": "array"},
                "match_score": {"type": "object"},
                "gaps_or_risks": {"type": "array"},
                "suggested_interview_questions": {"type": "array"},
                "compliance": {"type": "object"},
            },
        },
        risk_level="high",
        capabilities=["resume_qa", "jd_skill_matrix", "match_scoring", "compliance_guard", "evidence_pages"],
        example_queries=[
            "这个候选人适合 AI Agent 工程师岗位吗？",
            "总结他的 3 个亮点和 3 个风险点。",
            "根据这份简历生成 10 个面试问题。",
            "他的项目经历和 JD 匹配度怎么样？",
        ],
    )

    def run(self, query: str, context: SkillExecutionContext) -> SkillResult:
        sensitive_hits = _detect_sensitive(query)
        warnings = [
            "该 Skill 只提供辅助判断，不替代 HR 或用人经理的最终招聘决策。",
            "禁止依据年龄、性别、民族、婚育等敏感属性做判断。",
        ]
        compliance = {"sensitive_attributes_detected": sensitive_hits, "blocked": bool(sensitive_hits)}

        # 合规拦截：涉及敏感属性的判断请求直接拒绝据此评估。
        if sensitive_hits:
            warnings.append(
                "检测到问题涉及敏感属性(" + "、".join(sensitive_hits) + ")，已拒绝据此做招聘判断。"
            )
            return SkillResult(
                skill_name=self.spec.name,
                status="unsupported",
                answer="该问题涉及年龄/性别/民族/婚育等敏感属性，依据合规要求无法据此做招聘判断。请改为基于技能、经验与岗位要求的评估。",
                structured_data={"compliance": compliance},
                evidence_pages=[],
                warnings=warnings,
                next_actions=["请改用基于岗位技能、项目经历与能力要求的问题重新发起。"],
            )

        qa_result = context.engine.ask(query, topk=context.top_k, session_id=context.session_id)
        evidence_pages = build_evidence_pages(context.engine, qa_result)
        page_chunks = _joined_text_with_pages(context.engine, evidence_pages)
        full_text = "\n".join(c["content"] for c in page_chunks)
        target_role = _role_target(query)
        matrix = _skill_matrix(page_chunks, target_role)
        match_score = _match_score(matrix)
        matched_skills = _matched_skills(page_chunks)
        has_jd = "JD" in full_text.upper() or "岗位" in full_text
        summary = _candidate_summary(page_chunks, matched_skills)
        risks = _gaps_or_risks(matrix, matched_skills, has_jd)
        questions = _interview_questions(matched_skills, target_role)

        if not evidence_pages:
            warnings.append("当前没有找到简历/JD 类证据页，输出为通用模板结果。")

        return SkillResult(
            skill_name=self.spec.name,
            status="success" if evidence_pages else "need_more_info",
            answer=qa_result.answer if qa_result.answer else "已生成招聘辅助结构化结果，请结合证据页人工复核。",
            structured_data={
                "candidate_summary": summary,
                "matched_skills": matched_skills,
                "skill_matrix": matrix,
                "match_score": match_score,
                "target_role": target_role,
                "gaps_or_risks": risks,
                "suggested_interview_questions": questions,
                "compliance": compliance,
                "verified": qa_result.verified,
            },
            evidence_pages=evidence_pages,
            trace=build_trace_payload(qa_result, include_trace=bool(context.options.get("return_trace", True))),
            warnings=warnings,
            next_actions=[
                "若要获得更稳定的匹配报告，请同时提供岗位 JD 和候选人简历。",
                "对高风险判断结论请增加人工复核。",
            ],
        )
