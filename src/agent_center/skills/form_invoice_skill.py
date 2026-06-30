from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..schemas import SkillResult, SkillSpec
from ..skill_registry import SkillExecutionContext
from ..trace import build_evidence_pages, build_trace_payload


FIELD_KEYS = {
    "title": ("标题", "单据标题", "文档标题"),
    "company": ("公司", "供应商", "购买方", "销售方", "甲方", "乙方"),
    "amount": ("金额", "总额", "价税合计", "含税金额"),
    "date": ("日期", "发票日期", "签署日期", "开票日期"),
    "id_number": ("采购单号", "合同编号", "单号", "编号", "发票号"),
    "tax_id": ("税号", "纳税人识别号", "Tax ID"),
    "payment_terms": ("付款周期", "付款方式", "付款条件", "账期"),
}

# 敏感字段：原值需脱敏后再对外展示，并提示人工复核。
SENSITIVE_FIELDS = {"amount", "tax_id", "id_number"}


def _first_line(text: str) -> Optional[str]:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:80]
    return None


def _extract_company(text: str) -> Optional[str]:
    match = re.search(r"([一-鿿A-Za-z0-9（）()·\-.]{2,40}(?:有限公司|公司|集团|Inc\.|Ltd\.))", text)
    return match.group(1) if match else None


def _extract_amount(text: str) -> Optional[str]:
    match = re.search(r"(?:金额|总额|价税合计|含税金额)[^\d]{0,8}(\d+(?:\.\d{1,2})?)", text)
    return match.group(1) if match else None


def _extract_date(text: str) -> Optional[str]:
    match = re.search(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?)", text)
    return match.group(1) if match else None


def _extract_identifier(text: str) -> Optional[str]:
    match = re.search(r"\b([A-Z]{1,6}-\d{3,})\b", text.upper())
    return match.group(1) if match else None


def _extract_tax_id(text: str) -> Optional[str]:
    match = re.search(r"(?:纳税人识别号|税号|Tax ID)[^\w]{0,8}([0-9A-Z]{8,20})", text, re.IGNORECASE)
    return match.group(1) if match else None


def _extract_payment_terms(text: str) -> Optional[str]:
    match = re.search(r"((?:付款周期|付款方式|付款条件|账期)[^。；\n]{0,40})", text)
    return match.group(1).strip() if match else None


def _extract_risk_clauses(text: str) -> List[str]:
    clauses: List[str] = []
    for sentence in re.split(r"[。；\n]", text):
        cleaned = sentence.strip()
        if any(token in cleaned for token in ("违约责任", "违约金", "赔偿责任", "自动续约", "保密条款", "管辖法院")):
            clauses.append(cleaned[:120])
    return clauses[:5]


# ---- 字段级 verifier：返回 (是否通过, 提示) ----

def _verify_field(name: str, value: str) -> bool:
    if name == "amount":
        return bool(re.fullmatch(r"\d+(?:\.\d{1,2})?", value))
    if name == "date":
        return bool(re.search(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", value))
    if name == "tax_id":
        return bool(re.fullmatch(r"[0-9A-Z]{8,20}", value))
    if name == "id_number":
        return len(value) >= 3
    return bool(value and value.strip())


def _mask_value(name: str, value: str) -> str:
    """敏感字段脱敏：保留尾部少量字符，其余以 * 替代。"""
    if not value:
        return value
    if name == "amount":
        return "***"
    keep = 4 if len(value) > 6 else max(1, len(value) // 3)
    return "*" * (len(value) - keep) + value[-keep:]


def _new_field(value: Optional[Any], name: str, source: Optional[str]) -> Dict[str, Any]:
    """统一字段 schema：value/source/confidence/verified/masked。"""
    if value in (None, "", []):
        return {"value": None, "source": None, "confidence": "none", "verified": False, "masked": None}
    str_value = value if isinstance(value, str) else str(value)
    verified = _verify_field(name, str_value)
    masked = _mask_value(name, str_value) if name in SENSITIVE_FIELDS else str_value
    return {
        "value": value,
        "source": source,
        "confidence": "high" if verified else "low",
        "verified": verified,
        "masked": masked,
    }


def _extract_fields(engine: object, evidence_pages: List[Dict[str, object]]) -> Dict[str, Any]:
    raw: Dict[str, Any] = {
        "title": (None, None),
        "company": (None, None),
        "amount": (None, None),
        "date": (None, None),
        "id_number": (None, None),
        "tax_id": (None, None),
        "payment_terms": (None, None),
    }
    risk_clauses: List[str] = []

    def _set(name: str, value: Optional[Any], page_id: str) -> None:
        if value not in (None, "", []) and raw[name][0] is None:
            raw[name] = (value, page_id)

    for item in evidence_pages:
        page_id = item["page_id"]
        page = engine.retriever.get_page(page_id)
        fields = getattr(page, "fields", {}) or {}
        content = getattr(page, "content", "") or ""
        _set("title", _first_line(content) or fields.get("标题"), page_id)
        _set("company", next((fields.get(k) for k in FIELD_KEYS["company"] if fields.get(k)), None) or _extract_company(content), page_id)
        _set("amount", next((fields.get(k) for k in FIELD_KEYS["amount"] if fields.get(k)), None) or _extract_amount(content), page_id)
        _set("date", next((fields.get(k) for k in FIELD_KEYS["date"] if fields.get(k)), None) or _extract_date(content), page_id)
        _set("id_number", next((fields.get(k) for k in FIELD_KEYS["id_number"] if fields.get(k)), None) or _extract_identifier(content), page_id)
        _set("tax_id", next((fields.get(k) for k in FIELD_KEYS["tax_id"] if fields.get(k)), None) or _extract_tax_id(content), page_id)
        _set("payment_terms", next((fields.get(k) for k in FIELD_KEYS["payment_terms"] if fields.get(k)), None) or _extract_payment_terms(content), page_id)
        if not risk_clauses:
            risk_clauses = _extract_risk_clauses(content)

    structured = {name: _new_field(value, name, src) for name, (value, src) in raw.items()}
    structured["risk_clauses"] = risk_clauses
    return structured


def _answer_from_query(query: str, fields: Dict[str, Any]) -> str:
    def _val(name: str) -> Optional[Any]:
        return fields.get(name, {}).get("value") if isinstance(fields.get(name), dict) else None

    if "金额" in query:
        return f"提取到的金额为 {_val('amount')}（敏感字段，请脱敏后人工确认）。" if _val("amount") else "当前证据页中未稳定提取到金额字段。"
    if any(token in query for token in ("付款周期", "付款方式", "账期")):
        return f"付款条款为：{_val('payment_terms')}。" if _val("payment_terms") else "当前证据页中未找到明确付款条款。"
    if any(token in query for token in ("采购单号", "合同编号", "单号", "编号")):
        return f"识别到的单据编号为 {_val('id_number')}。" if _val("id_number") else "当前证据页中未找到明确单据编号。"
    if any(token in query for token in ("违约", "风险条款")):
        clauses = fields.get("risk_clauses") or []
        return "发现风险条款：" + "；".join(clauses) if clauses else "当前证据页中未识别到明确违约/风险条款。"
    return "已完成结构化抽取，请查看字段结果、置信度与证据页。"


class FormInvoiceSkill:
    spec = SkillSpec(
        name="form_invoice",
        display_name="合同 / 表单 / 发票抽取",
        description=(
            "基于页级检索做字段级抽取，每个字段返回 value/source/confidence/verified/masked，"
            "敏感字段自动脱敏并提示人工确认。字段逻辑已完备；生产前需接入真实 OCR 与字段级 gold 标注。"
        ),
        category="document_extraction",
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
                "fields": {
                    "type": "object",
                    "description": "每个字段为 {value, source, confidence, verified, masked}",
                },
                "schema_status": {"type": "string"},
                "sensitive_review_required": {"type": "boolean"},
                "evidence_pages": {"type": "array"},
                "warnings": {"type": "array"},
            },
        },
        risk_level="high",
        capabilities=["field_extraction", "field_verifier", "sensitive_masking", "evidence_pages"],
        example_queries=[
            "这张发票的金额是多少？",
            "这个合同的付款周期是什么？",
            "采购单号是多少？",
            "合同里有没有违约责任条款？",
        ],
    )

    def run(self, query: str, context: SkillExecutionContext) -> SkillResult:
        qa_result = context.engine.ask(
            query,
            topk=context.top_k,
            session_id=context.session_id,
            forced_branch="fact_qa",
        )
        evidence_pages = build_evidence_pages(context.engine, qa_result)
        fields = _extract_fields(context.engine, evidence_pages)
        answer = _answer_from_query(query, fields)
        warnings = [
            "字段抽取与字段级校验逻辑已完备，但生产前需接入真实 OCR 与字段级 gold 标注。",
            "金额、税号、单据编号等敏感字段已脱敏展示（masked），原值需在受控环境人工确认。",
        ]
        next_actions: List[str] = []

        low_conf = [name for name, f in fields.items() if isinstance(f, dict) and f.get("value") and not f.get("verified")]
        if low_conf:
            warnings.append("以下字段未通过格式校验，置信度较低，请人工复核：" + "、".join(low_conf) + "。")

        non_empty = [name for name, f in fields.items() if name != "risk_clauses" and isinstance(f, dict) and f.get("value")]
        if not evidence_pages:
            warnings.append("当前没有找到稳定 evidence pages。")
            next_actions.append("请上传相关合同、采购单或发票文件后重试。")
        if not non_empty and not fields.get("risk_clauses"):
            next_actions.append("可补充单据类型、字段名或页码，以提升字段定位准确率。")

        return SkillResult(
            skill_name=self.spec.name,
            status="success" if evidence_pages else "need_more_info",
            answer=answer,
            structured_data={
                "fields": fields,
                "schema_status": "field_level",
                "sensitive_review_required": True,
                "verified": qa_result.verified,
            },
            evidence_pages=evidence_pages,
            trace=build_trace_payload(qa_result, include_trace=bool(context.options.get("return_trace", True))),
            warnings=warnings,
            next_actions=next_actions,
        )
