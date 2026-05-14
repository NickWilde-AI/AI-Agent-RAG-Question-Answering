"""
eval_suite.py — 七类文档离线评测**套件**（批量调 QAEngine + 聚合指标）

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
- 持有 `DEFAULT_EVAL_SAMPLES`：每条含 `gold_pages` / `gold_answer` / `gold_branch` / `category`。
- 对每条样本调 `QAEngine.ask`，用 `eval_metrics` 计算 Recall@k、Accuracy、Router 准确率等。
- 对应简历「数据闭环 / 离线 batch 跑分」的工程落点（样本可换真实标注）。

================================================================================
【类比 Android】
================================================================================
- 像 **Macrobenchmark / 集成测试 suite**：遍历 scenario 列表，统一报表输出。
- `@dataclass(frozen=True) class EvalSample`：`frozen=True` 表示样本描述不可变，类似 `data class` + 全 val。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `@dataclass(frozen=True)`：实例字段赋值会抛错；适合值对象/DTO。
- `Sequence[str]`：金标页 id 列表可用 tuple 传入。

七类文档离线评测套件。

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
