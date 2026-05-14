"""
offer_agent_core — **聚合导出包**（给 `main.py` 等「面试友好 import 路径」用）

================================================================================
【在「简历第一条」里的位置】
================================================================================
- 本包**不实现**编排逻辑，只做 `from ..engine.xxx import` / `from ..eval_suite import` 的**门面（Facade）**。
- 真源实现主要在 `src/pipeline.py`、`src/retriever.py` 等；`src/engine/*.py` 多为 `from ..pipeline import *` 的薄转发层。

================================================================================
【类比 Android】
================================================================================
- 像 `api` 模块里的 `Api.kt` 只 `export` 各 feature 的 public 类型，给 app 模块一条依赖边。

================================================================================
【从 Java/Kotlin 读 Python】
================================================================================
- `__all__ = [...]`：显式列出 `from offer_agent_core import *` 时导出的符号；类似 Java `module-info exports` 的弱化版。

核心业务包名（面试表达友好）。
"""

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

