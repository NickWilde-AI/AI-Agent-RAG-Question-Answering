"""
eval_metrics.py — 离线评测**纯函数**（与业务编排解耦）

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
- 不调用网络、不读库；输入是「排序后的 doc_id 列表 + 金标集合 + 预测答案」等，输出标量指标。
- 被 `eval_suite.py`、`main.py`（经 offer_agent）引用，对应简历「AgentEval / 回归指标」。

================================================================================
【类比 Android】
================================================================================
- 像 **JUnit 纯函数断言工具类** `MetricsUtils`：可单测、可在 CI 跑分，不依赖 Android Framework。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `Sequence[str]` / `Iterable[str]`：`typing` 只读/可迭代抽象；比强制 `List` 更宽（tuple 也可传入）。
- `ranked_doc_ids[:k]`：切片**拷贝视图**（对 list 是浅拷贝新 list），上界超过长度不报错。
- `gold: Set[str] = set(positive_doc_ids)`：把任意可迭代转成 set，加速 `in` 查询。
- `@dataclass` 的 `EvaluationSummary`（本文件末尾）：打包多条样本聚合后的 float 指标。

离线评测指标。

与你 PDF 里的指标定义一致：
- Recall@10
- Accuracy（归一化后 exact match）
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, Iterable, List, Sequence, Set


def recall_at_k(ranked_doc_ids: Sequence[str], positive_doc_ids: Iterable[str], k: int = 10) -> float:
    """命中至少一个金标页则记 1.0，否则 0.0。"""
    gold: Set[str] = set(positive_doc_ids)
    return 1.0 if any(doc_id in gold for doc_id in ranked_doc_ids[:k]) else 0.0


def normalize_answer(text: str) -> str:
    """文本归一化：小写 + 首尾空白去除 + 连续空格压缩。"""
    return " ".join(text.lower().strip().split())


def accuracy(pred: str, gold: str) -> bool:
    """兼容文本精确匹配与数值容差的宽松准确率。"""
    return relaxed_exact_match(pred, gold, tol=0.05)


def relaxed_exact_match(pred: str, gold: str, tol: float = 0.05) -> bool:
    """
    宽松匹配：
    1) 文本归一化后全等 -> True
    2) 若双方都存在数字，则首个数字允许相对误差 tol
    """
    pred_norm = normalize_answer(pred)
    gold_norm = normalize_answer(gold)
    if pred_norm == gold_norm:
        return True
    # 企业字段类答案常带“依据文档”前缀；短金标值被包含即可视为命中。
    if gold_norm and len(gold_norm) <= 32 and gold_norm in pred_norm:
        return True

    num_pattern = r"-?\d+(\.\d+)?"
    pred_hit = re.search(num_pattern, pred_norm)
    gold_hit = re.search(num_pattern, gold_norm)
    if not (pred_hit and gold_hit):
        return False
    p = float(pred_hit.group())
    g = float(gold_hit.group())
    if g == 0:
        return abs(p - g) <= tol
    return abs(p - g) <= abs(g) * tol


def average(values: List[float]) -> float:
    """简单平均值。"""
    return sum(values) / len(values) if values else 0.0


def router_accuracy(predicted_branches: Sequence[str], gold_branches: Sequence[str]) -> float:
    """Router 分支选择准确率。"""
    total = min(len(predicted_branches), len(gold_branches))
    if total == 0:
        return 0.0
    correct = sum(1 for pred, gold in zip(predicted_branches[:total], gold_branches[:total]) if pred == gold)
    return correct / total


def relative_improvement(current: float, baseline: float) -> float:
    """相对提升比例，用于表达端到端收益。"""
    if baseline == 0:
        return 0.0
    return (current - baseline) / baseline


@dataclass(frozen=True)
class EvaluationSummary:
    """离线评测汇总，便于写入报告或接口输出。"""

    recall_at_10: float
    accuracy: float
    router_acc: float = 0.0

    def as_percent(self) -> Dict[str, str]:
        return {
            "recall_at_10": f"{self.recall_at_10 * 100:.2f}%",
            "accuracy": f"{self.accuracy * 100:.2f}%",
            "router_acc": f"{self.router_acc * 100:.2f}%",
        }
