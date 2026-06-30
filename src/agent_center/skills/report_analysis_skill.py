from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..schemas import SkillResult, SkillSpec
from ..skill_registry import SkillExecutionContext
from ..trace import build_evidence_pages, build_trace_payload


# 单位归一：把“万/亿/千”后缀换算成绝对数值，便于跨页比较与计算。
_UNIT_FACTORS = {
    "亿": 1_0000_0000.0,
    "万": 1_0000.0,
    "千": 1_000.0,
}

# 形如「海外员工 35%」「主机厂 120 家」「营收 18.5 亿」的指标—数值对。
_METRIC_VALUE_RE = re.compile(
    r"([一-鿿A-Za-z（）()·\-_/]{2,20})\s*[:：]?\s*"
    r"(\d+(?:\.\d+)?)\s*(亿|万|千)?\s*(%|％|人|家|个|元|项)?"
)


def _normalize_value(raw: str, unit: Optional[str]) -> float:
    value = float(raw)
    if unit and unit in _UNIT_FACTORS:
        value *= _UNIT_FACTORS[unit]
    return value


def _best_matching_keys(query: str, keys: List[str], limit: int = 2) -> List[str]:
    scored: List[Tuple[int, str]] = []
    for key in keys:
        score = 0
        if key in query:
            score += 10
        compact = key.replace(" ", "")
        if compact and compact in query.replace(" ", ""):
            score += 5
        for token in [x for x in compact.replace("（", "").replace("）", "").replace("(", "").replace(")", "").split("/") if x]:
            if token in query and len(token) > 1:
                score += 2
        if score:
            scored.append((score, key))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[1] for item in scored[:limit]]


def _collect_chart_data(engine: object, evidence_pages: List[Dict[str, object]]) -> Dict[str, float]:
    """优先采用结构化 chart_data；缺失时从证据页正文正则兜底抽取指标-数值。"""
    merged: Dict[str, float] = {}
    text_metrics: Dict[str, float] = {}
    for item in evidence_pages:
        page = engine.retriever.get_page(item["page_id"])
        for key, value in getattr(page, "chart_data", {}).items():
            merged[key] = float(value)
        content = getattr(page, "content", "") or ""
        for metric, raw, unit, _suffix in _METRIC_VALUE_RE.findall(content):
            metric = metric.strip("：: ")
            if not metric or metric in merged or metric in text_metrics:
                continue
            try:
                text_metrics[metric] = _normalize_value(raw, unit or None)
            except ValueError:
                continue
    # 结构化数据优先级更高，正文兜底只补充缺失项。
    for key, value in text_metrics.items():
        merged.setdefault(key, value)
    return merged


def _calculation_from_query(
    query: str, merged: Dict[str, float]
) -> Tuple[Optional[str], Optional[Dict[str, object]]]:
    if not merged:
        return None, None
    matches = _best_matching_keys(query, list(merged))
    total = sum(merged.values())
    if "占比" in query and matches and total > 0:
        key = matches[0]
        value = merged[key]
        percent = round(value / total * 100, 2)
        return f"{key}占比约 {percent}%", {
            "type": "percentage",
            "metric": key,
            "value": value,
            "total": total,
            "result": percent,
            "unit": "%",
            "formula": f"{value} / {total} * 100",
            "inputs": {key: value, "total": total},
            "confidence": "high" if key in query else "medium",
        }
    if any(token in query for token in ("多多少", "差值", "差多少", "相差")) and len(matches) >= 2:
        # 差值方向取决于谁先在问题中出现：「A 比 B 多多少」中 A 为被比较主体。
        ordered = sorted(matches[:2], key=lambda k: query.find(k) if query.find(k) >= 0 else len(query))
        left, right = ordered[0], ordered[1]
        diff = round(merged[left] - merged[right], 4)
        return f"{left}比{right}{'多' if diff >= 0 else '少'} {abs(diff)}", {
            "type": "difference",
            "left_metric": left,
            "left_value": merged[left],
            "right_metric": right,
            "right_value": merged[right],
            "result": diff,
            "formula": f"{merged[left]} - {merged[right]}",
            "inputs": {left: merged[left], right: merged[right]},
            "confidence": "high",
        }
    if any(token in query for token in ("总和", "合计", "一共", "总计")):
        return f"相关指标合计为 {round(total, 4)}", {
            "type": "sum",
            "result": round(total, 4),
            "formula": " + ".join(str(v) for v in merged.values()),
            "inputs": dict(merged),
            "confidence": "medium",
        }
    if any(token in query for token in ("最高", "最大")):
        key = max(merged, key=merged.get)
        return f"{key}最高，数值为 {merged[key]}", {
            "type": "max",
            "metric": key,
            "result": merged[key],
            "inputs": dict(merged),
            "confidence": "high",
        }
    if any(token in query for token in ("最低", "最小")):
        key = min(merged, key=merged.get)
        return f"{key}最低，数值为 {merged[key]}", {
            "type": "min",
            "metric": key,
            "result": merged[key],
            "inputs": dict(merged),
            "confidence": "high",
        }
    if matches:
        key = matches[0]
        return f"{key}对应数值为 {merged[key]}", {
            "type": "lookup",
            "metric": key,
            "result": merged[key],
            "inputs": {key: merged[key]},
            "confidence": "medium",
        }
    return None, None


class ReportAnalysisSkill:
    spec = SkillSpec(
        name="report_analysis",
        display_name="企业报表分析",
        description=(
            "复用 chart_qa、VLM 页图理解和页级证据，支持占比/差值/极值/求和等数值计算并给出可追溯公式。"
            "计算逻辑已完备；生产前仍需在更大规模真实报表与 gold set 上扩充口径覆盖。"
        ),
        category="analytics",
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
                "answer": {"type": "string"},
                "calculation": {
                    "type": ["object", "null"],
                    "properties": {
                        "type": {"type": "string"},
                        "result": {"type": ["number", "string"]},
                        "formula": {"type": "string"},
                        "inputs": {"type": "object"},
                        "confidence": {"type": "string"},
                    },
                },
                "chart_metrics": {"type": "object"},
                "warnings": {"type": "array"},
            },
        },
        risk_level="medium",
        capabilities=["chart_qa", "vlm_page_reasoning", "numeric_extraction", "evidence_pages"],
        example_queries=[
            "海外员工占比是多少？",
            "主机厂数量比一级供应商多多少？",
            "这个图表反映了什么趋势？",
        ],
    )

    def run(self, query: str, context: SkillExecutionContext) -> SkillResult:
        qa_result = context.engine.ask(
            query,
            topk=context.top_k,
            session_id=context.session_id,
            forced_branch="chart_qa",
        )
        evidence_pages = build_evidence_pages(context.engine, qa_result)
        merged = _collect_chart_data(context.engine, evidence_pages)
        calculated_answer, calculation = _calculation_from_query(query, merged)
        warnings = ["当前报表分析 Skill 计算逻辑已完备，但复杂跨页计算和 BI 口径仍需更大规模 gold set 验证。"]
        next_actions: List[str] = []
        status = "success"
        answer = calculated_answer or qa_result.answer
        if calculation is None:
            if merged:
                warnings.append("已读取到指标数值，但当前问题未匹配到可执行的计算类型，已回退为 chart_qa / RAG 答案。")
            else:
                warnings.append("未能从当前证据页稳定提取指标数值，已回退为 chart_qa / RAG 答案。")
        if not evidence_pages:
            status = "need_more_info"
            warnings.append("当前没有找到可追溯的证据页。")
            next_actions.append("请指定报表文件、页码或更明确的指标名称。")
        elif "无法稳定读数" in qa_result.answer or "未找到足够依据" in qa_result.answer:
            status = "need_more_info"
            next_actions.append("补充更具体的指标名，或上传包含图表页的资料后重试。")
        if not qa_result.verified:
            warnings.append("当前回答未通过完整 verifier 校验，请人工复核关键数值。")
        return SkillResult(
            skill_name=self.spec.name,
            status=status,
            answer=answer,
            structured_data={
                "branch": qa_result.branch,
                "verified": qa_result.verified,
                "calculation": calculation,
                "chart_metrics": merged,
            },
            evidence_pages=evidence_pages,
            trace=build_trace_payload(qa_result, include_trace=bool(context.options.get("return_trace", True))),
            warnings=warnings,
            next_actions=next_actions,
        )
