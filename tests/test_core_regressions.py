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
