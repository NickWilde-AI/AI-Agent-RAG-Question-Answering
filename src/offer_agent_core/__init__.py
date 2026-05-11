"""核心业务包名（面试表达友好）。"""

from ..core.config import SETTINGS, Settings
from ..core.models import Page, QAResult, RetrievalHit
from ..engine.agent_loop import LoopRunResult, LoopStep, PlanExecuteAgentLoop
from ..engine.bootstrap import build_agent_loop, build_engine
from ..engine.eval_metrics import accuracy, average, recall_at_k
from ..eval_suite import DEFAULT_EVAL_SAMPLES, EvalSample, run_eval_suite
from ..engine.llm_client import LLMClient
from ..engine.memory import SessionMemory
from ..engine.pipeline import QAEngine
from ..engine.retriever import PageRetriever
from ..engine.router import RouterAgent
from ..engine.tools import chart_qa, fact_qa, multi_page_qa, translate_qa
from ..engine.verifier import Verifier
from ..services import ChartParsingClient, ColPaliRerankClient, MultimodalEmbeddingClient, TranslationEngineClient, VLMClient
from ..infra.pdf_ingest import ingest_pdf_with_pymupdf
from ..infra.vector_store import InMemoryVectorStore, MilvusVectorStore

__all__ = [
    "SETTINGS",
    "Settings",
    "LLMClient",
    "PlanExecuteAgentLoop",
    "LoopStep",
    "LoopRunResult",
    "Page",
    "RetrievalHit",
    "QAResult",
    "SessionMemory",
    "PageRetriever",
    "RouterAgent",
    "Verifier",
    "QAEngine",
    "build_engine",
    "build_agent_loop",
    "ingest_pdf_with_pymupdf",
    "InMemoryVectorStore",
    "MilvusVectorStore",
    "recall_at_k",
    "accuracy",
    "average",
    "fact_qa",
    "multi_page_qa",
    "chart_qa",
    "translate_qa",
    "MultimodalEmbeddingClient",
    "ColPaliRerankClient",
    "VLMClient",
    "ChartParsingClient",
    "TranslationEngineClient",
    "EvalSample",
    "DEFAULT_EVAL_SAMPLES",
    "run_eval_suite",
]

