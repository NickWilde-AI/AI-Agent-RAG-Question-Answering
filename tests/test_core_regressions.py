from concurrent.futures import ThreadPoolExecutor

from src.langgraph_engine import LangGraphQAEngine
from src.memory import SessionMemory
from src.models import Page, QAResult
from src.retriever import PageRetriever
from src.router import RouterAgent
from src.verifier import Verifier


def test_langgraph_smalltalk_gate_no_unbound_result():
    pages=[Page(page_id="p1",doc_id="d1",doc_type="report",language="zh",content="产品线A销售额100",chart_data={"产品线A":100})]
    engine=LangGraphQAEngine(retriever=PageRetriever(pages),router=RouterAgent(),memory=SessionMemory(),verifier=Verifier())
    result=engine.ask("你好",session_id="smalltalk")
    assert result.trace.retry_reason == "smalltalk_blocked"
    assert result.hits == []


def test_session_memory_concurrent_history_is_bounded():
    memory=SessionMemory(max_history=20,cache_verified_only=False)
    def add(i):
        memory.add_record(QAResult(query=f"q{i}",rewritten_query=f"q{i}",branch="fact_qa",answer="a",verified=True,hits=[]),session_id="s")
    with ThreadPoolExecutor(max_workers=8) as pool: list(pool.map(add,range(200)))
    assert len(memory.get_recent_history("s",limit=1000)) == 20


def test_direct_qwen_vlm_adapter(monkeypatch):
    class FakeDirect:
        enabled=True
        def answer(self,query,image_paths,mode): return "视觉答案"
        def verify(self,query,answer,image_paths): return True
    from src.services import VLMClient
    client=VLMClient(api_url="")
    client._direct=FakeDirect()
    client._verifier_direct=FakeDirect()
    assert client.enabled
    assert client.answer("问题",["page.png"],"single_page") == "视觉答案"
    assert client.verify("问题","视觉答案",["page.png"]) is True


def test_explicit_page_number_is_a_generic_retrieval_signal():
    pages=[
        Page(page_id="d_p12",doc_id="d",doc_type="ppt",language="zh",content="相同的页面内容",page_no=12),
        Page(page_id="d_p13",doc_id="d",doc_type="ppt",language="zh",content="相同的页面内容",page_no=13),
    ]
    _,hits=PageRetriever(pages).retrieve("请查看第13页",topk=1)
    assert hits[0].page_id == "d_p13"


def test_company_introduction_does_not_force_ppt_prefilter():
    pages=[Page(page_id="company",doc_id="d",doc_type="report",language="zh",content="中科创达是智能操作系统产品和技术提供商")]
    retriever=PageRetriever(pages)
    assert retriever.infer_doc_type("介绍一下中科创达") is None
    _,hits=retriever.retrieve("介绍一下中科创达",topk=1)
    assert hits and hits[0].page_id=="company"


def test_explicit_ppt_request_keeps_ppt_prefilter():
    assert PageRetriever.infer_doc_type("请概括这份PPT的内容") == "ppt"


def test_visual_rerank_blends_model_rank_without_binding_score_scale():
    from src.models import RetrievalHit
    candidates=[RetrievalHit("coarse_first",9.7),RetrievalHit("visual_first",0.2)]
    reranked=PageRetriever._blend_rerank_scores(candidates,{"visual_first":0.99,"coarse_first":0.1})
    assert reranked[0].page_id == "visual_first"


def test_visual_shortlist_reserves_space_for_exact_product_anchor():
    from src.models import RetrievalHit
    pages=[Page(page_id=f"noise{i}",doc_id="noise",doc_type="ppt",language="zh",content="通用介绍") for i in range(8)]
    pages.append(Page(page_id="exact",doc_id="target",doc_type="ppt",language="zh",content="AquaOS 三项核心能力"))
    retriever=PageRetriever(pages)
    candidates=[RetrievalHit(f"noise{i}",10-i) for i in range(8)]+[RetrievalHit("exact",0.1)]
    shortlist=retriever._visual_rerank_shortlist("AquaOS是什么？",candidates,cap=4)
    assert "exact" in {hit.page_id for hit in shortlist}


def test_fact_evidence_keeps_cross_document_top_hits():
    from src.bootstrap import build_engine_from_pages
    pages=[
        Page(page_id="a1",doc_id="a",doc_type="ppt",language="zh",content="噪声",page_no=1),
        Page(page_id="a2",doc_id="a",doc_type="ppt",language="zh",content="邻页",page_no=2),
        Page(page_id="b9",doc_id="b",doc_type="ppt",language="zh",content="正确证据",page_no=9),
    ]
    engine=build_engine_from_pages(pages)
    evidence=engine._expand_pages_for_evidence([pages[0],pages[2]])
    assert [page.page_id for page in evidence[:2]] == ["a1","b9"]


def test_deployed_vlm_gateway_keeps_visual_rerank_interface(monkeypatch):
    import src.services as services
    monkeypatch.setattr(services,"post_json",lambda url,payload:{"scores":{"p2":0.9,"p1":0.2}})
    client=services.VLMClient(api_url="http://vlm-gateway")
    scores=client.rerank_pages("问题",[{"page_id":"p1"},{"page_id":"p2"}])
    assert scores == {"p2":0.9,"p1":0.2}
