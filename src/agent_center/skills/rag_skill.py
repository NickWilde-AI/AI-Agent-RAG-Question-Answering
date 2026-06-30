from __future__ import annotations

from ..schemas import SkillResult, SkillSpec
from ..skill_registry import SkillExecutionContext
from ..trace import build_evidence_pages, build_trace_payload


class RAGSkill:
    spec = SkillSpec(
        name="rag",
        display_name="企业知识库问答",
        description="封装现有多模态 RAG 问答链路，返回答案、证据页和 route/retrieval/verifier trace。",
        category="knowledge",
        status="implemented",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "workspace_id": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                "options": {"type": "object"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "evidence_pages": {"type": "array"},
                "trace": {"type": "object"},
            },
        },
        risk_level="medium",
        capabilities=["hybrid_retrieval", "query_rewrite", "fact_qa", "multi_page_qa", "chart_qa", "verifier"],
        example_queries=[
            "这个项目的交付时间是什么？",
            "这个规格书里某个接口参数是什么意思？",
            "第 8 页提到的 347+ 量产平台项目是什么意思？",
        ],
    )

    def run(self, query: str, context: SkillExecutionContext) -> SkillResult:
        qa_result = context.engine.ask(query, topk=context.top_k, session_id=context.session_id)
        evidence_pages = build_evidence_pages(context.engine, qa_result)
        warnings = []
        next_actions = []
        if not qa_result.verified:
            warnings.append("当前回答未通过完整证据校验，建议缩小问题范围或指定文档后复核。")
            next_actions.append("补充文档名、页码或更明确的字段名后重试。")
        if not evidence_pages:
            warnings.append("当前回答缺少稳定 evidence pages。")
        return SkillResult(
            skill_name=self.spec.name,
            status="success" if qa_result.answer else "need_more_info",
            answer=qa_result.answer,
            structured_data={
                "branch": qa_result.branch,
                "rewritten_query": qa_result.rewritten_query,
                "verified": qa_result.verified,
                "source_files": qa_result.source_files,
            },
            evidence_pages=evidence_pages,
            trace=build_trace_payload(qa_result, include_trace=bool(context.options.get("return_trace", True))),
            warnings=warnings,
            next_actions=next_actions,
        )
