"""
离线评测指标。

与你 PDF 里的指标定义一致：
- Recall@10
- Accuracy（归一化后 exact match）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Set


def recall_at_k(ranked_doc_ids: Sequence[str], positive_doc_ids: Iterable[str], k: int = 10) -> float:
    """命中至少一个金标页则记 1.0，否则 0.0。"""
    gold: Set[str] = set(positive_doc_ids)
    return 1.0 if any(doc_id in gold for doc_id in ranked_doc_ids[:k]) else 0.0


def normalize_answer(text: str) -> str:
    """文本归一化：小写 + 首尾空白去除 + 连续空格压缩。"""
    return " ".join(text.lower().strip().split())


def accuracy(pred: str, gold: str) -> bool:
    """归一化后完全相等即正确。"""
    return normalize_answer(pred) == normalize_answer(gold)


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


def translation_engine_accuracy(agent_picks: Sequence[str], offline_best: Sequence[str]) -> float:
    """翻译分支中，在线选优引擎与离线最优引擎的一致率。"""
    return router_accuracy(agent_picks, offline_best)


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
    translate_engine_acc: float = 0.0

    def as_percent(self) -> Dict[str, str]:
        return {
            "recall_at_10": f"{self.recall_at_10 * 100:.2f}%",
            "accuracy": f"{self.accuracy * 100:.2f}%",
            "router_acc": f"{self.router_acc * 100:.2f}%",
            "translate_engine_acc": f"{self.translate_engine_acc * 100:.2f}%",
        }
