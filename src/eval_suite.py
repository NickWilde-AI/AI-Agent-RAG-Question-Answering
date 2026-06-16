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

该模块承接项目 PDF 中的 Recall@10、Accuracy、Router 决策准确率等指标。
真实生产环境可以把样本加载替换成标注平台或对象存储中的评测集。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence

from .eval_metrics import EvaluationSummary, accuracy, average, recall_at_k, router_accuracy
from .pipeline import QAEngine


@dataclass(frozen=True)
class EvalSample:
    query: str
    gold_pages: Sequence[str]
    gold_answer: str
    gold_branch: str
    category: str


@dataclass(frozen=True)
class EvalCategorySummary:
    """单个类别的离线评测结果。"""

    category: str
    sample_count: int
    recall_at_10: float
    accuracy: float
    router_acc: float
    verifier_pass_rate: float
    fallback_rate: float


@dataclass(frozen=True)
class EvalEngineeringSummary:
    """工程侧指标汇总。"""

    sample_count: int
    verifier_pass_rate: float
    fallback_rate: float
    cache_hit_rate: float
    avg_stage_latency_ms: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalRunReport:
    """完整评测报告：总体 + 分类 + 工程。"""

    overall: EvaluationSummary
    per_category: List[EvalCategorySummary]
    engineering: EvalEngineeringSummary


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
        query="华东区负责人是谁？",
        gold_pages=["report_p1"],
        gold_answer="李雷",
        gold_branch="fact_qa",
        category="业务图表与报表",
    ),
    EvalSample(
        query="采购申请单的发票日期是什么？",
        gold_pages=["form_p3"],
        gold_answer="2024-09-03",
        gold_branch="fact_qa",
        category="合同与工业表单",
    ),
    EvalSample(
        query="产品线B销售额是多少？",
        gold_pages=["report_p1"],
        gold_answer="180",
        gold_branch="chart_qa",
        category="业务图表与报表",
    ),
    EvalSample(
        query="故障代码 E-203 是什么？",
        gold_pages=["manual_en_p8"],
        gold_answer="E-203",
        gold_branch="fact_qa",
        category="英文手册",
    ),
]


def _build_category_summary(category: str, samples: List[EvalSample], results: List) -> EvalCategorySummary:
    recalls: List[float] = []
    accs: List[float] = []
    pred_branches: List[str] = []
    gold_branches: List[str] = []
    verified_count = 0
    fallback_count = 0

    for sample, result in zip(samples, results):
        recalls.append(recall_at_k([h.page_id for h in result.hits], sample.gold_pages, k=10))
        accs.append(1.0 if accuracy(result.answer, sample.gold_answer) else 0.0)
        pred_branches.append(result.branch)
        gold_branches.append(sample.gold_branch)
        if result.verified:
            verified_count += 1
        if result.trace and result.trace.fallback_triggered:
            fallback_count += 1

    total = len(samples)
    return EvalCategorySummary(
        category=category,
        sample_count=total,
        recall_at_10=average(recalls),
        accuracy=average(accs),
        router_acc=router_accuracy(pred_branches, gold_branches),
        verifier_pass_rate=(verified_count / total) if total else 0.0,
        fallback_rate=(fallback_count / total) if total else 0.0,
    )


def run_eval_report(engine: QAEngine, samples: Iterable[EvalSample] = DEFAULT_EVAL_SAMPLES) -> EvalRunReport:
    """
    执行离线评测并返回完整报告：
    - overall: 总体 Recall/Accuracy/RouterAcc
    - per_category: 各类别细分表现
    - engineering: fallback、verifier、cache、阶段耗时
    """
    sample_list = list(samples)
    results = [engine.ask(sample.query, topk=10) for sample in sample_list]

    recalls: List[float] = []
    accs: List[float] = []
    predicted_branches: List[str] = []
    gold_branches: List[str] = []

    verified_count = 0
    fallback_count = 0
    cache_hit_count = 0
    stage_cost_map: Dict[str, List[int]] = {}

    for sample, result in zip(sample_list, results):
        recalls.append(recall_at_k([h.page_id for h in result.hits], sample.gold_pages, k=10))
        accs.append(1.0 if accuracy(result.answer, sample.gold_answer) else 0.0)
        predicted_branches.append(result.branch)
        gold_branches.append(sample.gold_branch)

        if result.verified:
            verified_count += 1
        if result.branch == "cache_hit":
            cache_hit_count += 1
        if result.trace:
            if result.trace.fallback_triggered:
                fallback_count += 1
            for st in result.trace.stages:
                stage_cost_map.setdefault(st.stage, []).append(st.elapsed_ms)

    total = len(sample_list)
    overall = EvaluationSummary(
        recall_at_10=average(recalls),
        accuracy=average(accs),
        router_acc=router_accuracy(predicted_branches, gold_branches),
    )

    category_samples: Dict[str, List[EvalSample]] = {}
    category_results: Dict[str, List] = {}
    for sample, result in zip(sample_list, results):
        category_samples.setdefault(sample.category, []).append(sample)
        category_results.setdefault(sample.category, []).append(result)

    per_category = [
        _build_category_summary(cat, category_samples[cat], category_results[cat])
        for cat in sorted(category_samples.keys())
    ]
    engineering = EvalEngineeringSummary(
        sample_count=total,
        verifier_pass_rate=(verified_count / total) if total else 0.0,
        fallback_rate=(fallback_count / total) if total else 0.0,
        cache_hit_rate=(cache_hit_count / total) if total else 0.0,
        avg_stage_latency_ms={
            name: round(average([float(v) for v in values]), 2) for name, values in stage_cost_map.items()
        },
    )
    return EvalRunReport(overall=overall, per_category=per_category, engineering=engineering)


def run_eval_suite(engine: QAEngine, samples: Iterable[EvalSample] = DEFAULT_EVAL_SAMPLES) -> EvaluationSummary:
    """兼容旧接口：返回总体指标。"""
    return run_eval_report(engine, samples=samples).overall
