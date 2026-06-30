from fastapi.testclient import TestClient

import src.api as api
from src.agent_center import AgentCenterRuntime
from src.agent_center.runtime import build_default_skill_registry
from src.bootstrap import build_engine


def test_skill_registry_returns_four_skills():
    registry = build_default_skill_registry()
    names = [skill.name for skill in registry.list_specs()]
    assert names == ["form_invoice", "hr_recruiting", "rag", "report_analysis"]


def test_agent_center_endpoints_and_rag_compatibility():
    old_engine = api.engine
    old_runtime = api.agent_center_runtime
    api.engine = build_engine("data/demo_pages.json")
    api.agent_center_runtime = AgentCenterRuntime(lambda: api.engine, lambda: api.research_executor)
    try:
        client = TestClient(api.app)

        listed = client.get("/agent-center/skills")
        assert listed.status_code == 200
        payload = listed.json()
        assert {item["name"] for item in payload} == {"rag", "report_analysis", "form_invoice", "hr_recruiting"}

        rag = client.post(
            "/agent-center/run",
            json={"skill_name": "rag", "query": "2024Q3 经营分析里哪个产品线销售额最高？", "top_k": 3, "options": {"return_trace": True}},
        )
        assert rag.status_code == 200
        rag_payload = rag.json()
        assert rag_payload["skill_name"] == "rag"
        assert rag_payload["status"] == "success"
        assert rag_payload["evidence_pages"]
        assert rag_payload["trace"]["branch"] in {"fact_qa", "chart_qa", "multi_page_qa", "cache_hit"}

        report = client.post(
            "/agent-center/run",
            json={"skill_name": "report_analysis", "query": "2024Q3 经营分析里哪个产品线销售额最高？", "top_k": 3},
        )
        assert report.status_code == 200
        report_payload = report.json()
        assert report_payload["skill_name"] == "report_analysis"
        assert "calculation" in report_payload["structured_data"]
        assert "chart_metrics" in report_payload["structured_data"]

        form = client.post(
            "/agent-center/run",
            json={"skill_name": "form_invoice", "query": "采购单号是多少？", "top_k": 3},
        )
        assert form.status_code == 200
        form_payload = form.json()
        id_field = form_payload["structured_data"]["fields"]["id_number"]
        # 字段级 schema：value 保留原值，masked 为脱敏视图，并通过格式校验。
        assert id_field["value"] == "PO-78421"
        assert id_field["verified"] is True
        assert id_field["masked"] != "PO-78421"
        assert form_payload["structured_data"]["sensitive_review_required"] is True

        hr = client.post(
            "/agent-center/run",
            json={"skill_name": "hr_recruiting", "query": "这个候选人适合 AI Agent 工程师岗位吗？", "top_k": 3},
        )
        assert hr.status_code == 200
        hr_payload = hr.json()
        assert {"candidate_summary", "matched_skills", "gaps_or_risks", "suggested_interview_questions", "skill_matrix", "match_score"} <= set(hr_payload["structured_data"])
        assert isinstance(hr_payload["structured_data"]["match_score"]["score"], (int, float))

        # 合规拦截：涉及敏感属性的提问应被拒绝据此判断。
        hr_blocked = client.post(
            "/agent-center/run",
            json={"skill_name": "hr_recruiting", "query": "这个候选人年龄多大，适合吗？", "top_k": 3},
        )
        assert hr_blocked.status_code == 200
        blocked_payload = hr_blocked.json()
        assert blocked_payload["status"] == "unsupported"
        assert blocked_payload["structured_data"]["compliance"]["blocked"] is True

        ask = client.post("/ask", json={"query": "采购申请单的采购单号是多少？"})
        assert ask.status_code == 200
        assert "PO-78421" in ask.json()["answer"]
    finally:
        api.engine = old_engine
        api.agent_center_runtime = old_runtime


def test_agent_platform_html_route():
    client = TestClient(api.app)
    response = client.get("/agent-platform")
    assert response.status_code == 200
    assert "企业级 AI Agent 中台" in response.text
    assert "/agent-center/run" in response.text


def test_report_analysis_gold_set():
    import json
    from src.agent_center.skills.report_analysis_skill import _calculation_from_query

    gold = json.load(open("data/agent_center/report_analysis_gold.json", encoding="utf-8"))
    for sample in gold["samples"]:
        _, calc = _calculation_from_query(sample["query"], sample["metrics"])
        assert calc is not None, sample["id"]
        assert calc["type"] == sample["expected_type"], sample["id"]
        assert abs(float(calc["result"]) - sample["expected_result"]) < 1e-6, sample["id"]


def test_hr_compliance_samples():
    import json
    from src.agent_center.skills.hr_recruiting_skill import _detect_sensitive

    gold = json.load(open("data/agent_center/hr_compliance_samples.json", encoding="utf-8"))
    for sample in gold["samples"]:
        blocked = bool(_detect_sensitive(sample["query"]))
        assert blocked == sample["must_block"], sample["id"]


def test_form_invoice_field_verifier_and_masking():
    from src.agent_center.skills.form_invoice_skill import _new_field

    amount_ok = _new_field("1234.50", "amount", "p1")
    assert amount_ok["verified"] is True and amount_ok["masked"] == "***"

    amount_bad = _new_field("一千元", "amount", "p1")
    assert amount_bad["verified"] is False and amount_bad["confidence"] == "low"

    tax = _new_field("91110108MA01XYZ12", "tax_id", "p1")
    assert tax["verified"] is True and tax["masked"].endswith("YZ12") and tax["masked"] != tax["value"]

    empty = _new_field(None, "company", None)
    assert empty["value"] is None and empty["confidence"] == "none"
