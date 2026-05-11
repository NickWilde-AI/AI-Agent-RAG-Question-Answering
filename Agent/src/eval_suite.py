"""七类文档离线评测套件。

该模块承接项目 PDF 中的 Recall@10、Accuracy、Router 决策准确率和翻译引擎选择准确率。
真实生产环境可以把样本加载替换成标注平台或对象存储中的评测集。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .eval_metrics import EvaluationSummary, accuracy, average, recall_at_k, router_accuracy
from .pipeline import QAEngine


@dataclass(frozen=True)
class EvalSample:
    query: str
    gold_pages: Sequence[str]
    gold_answer: str
    gold_branch: str
    category: str


DEFAULT_EVAL_SAMPLES = [
    EvalSample(
        query="2024Q3 经营分析里哪个产品线销售额最高？",
        gold_pages=["report_p1"],
        gold_answer="产品线B（180）",
        gold_branch="chart_qa",
        category="业务图表与报表",
    ),
    EvalSample(
        query="采购申请单的采购单号是多少？",
        gold_pages=["form_p3"],
        gold_answer="PO-78421",
        gold_branch="fact_qa",
        category="合同与工业表单",
    ),
    EvalSample(
        query="谁负责介绍了实验/试点安排？",
        gold_pages=["ppt_p2"],
        gold_answer="张三",
        gold_branch="multi_page_qa",
        category="培训与汇报 PPT",
    ),
    EvalSample(
        query="故障代码 E-203 的中文含义是什么？",
        gold_pages=["manual_en_p8"],
        gold_answer="[engine=gpt4o] Fault code E-203: 主轴温度过高, 需立即停机检修.",
        gold_branch="translate_qa",
        category="跨语种手册翻译",
    ),
]


def run_eval_suite(engine: QAEngine, samples: Iterable[EvalSample] = DEFAULT_EVAL_SAMPLES) -> EvaluationSummary:
    """执行离线评测并返回汇总指标。"""
    recalls: List[float] = []
    accs: List[float] = []
    predicted_branches: List[str] = []
    gold_branches: List[str] = []

    for sample in samples:
        result = engine.ask(sample.query, topk=10)
        recalls.append(recall_at_k([h.page_id for h in result.hits], sample.gold_pages, k=10))
        accs.append(1.0 if accuracy(result.answer, sample.gold_answer) else 0.0)
        predicted_branches.append(result.branch)
        gold_branches.append(sample.gold_branch)

    return EvaluationSummary(
        recall_at_10=average(recalls),
        accuracy=average(accs),
        router_acc=router_accuracy(predicted_branches, gold_branches),
    )
