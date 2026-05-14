"""
main.py — 命令行演示入口（等同「跑一遍集成测试 + 离线评测」）

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
- 通过 `offer_agent_core.build_engine` 组装与 `bootstrap.build_engine` 同构的引擎（注意：本文件 import 路径是 `src.offer_agent_core`，与 `uvicorn src.api:app` 入口可并存）。
- `run_demo`：打印多条典型 query 的 `rewritten_query` / `branch` / `hits` / `verified`，用于面试口述对齐代码。
- `run_offline_eval`：对应简历「AgentEval / Recall@10 / Accuracy」的**骨架演示**。

================================================================================
【类比 Android】
================================================================================
- 像 `Application` 里开发阶段调用的 **Debug 启动自检** 或 `androidTest` 里 `@Test fun smokePipeline()`。
- `print_result`：等同 Logcat 结构化日志，把一次请求的「可解释字段」打全。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `if __name__ == "__main__":`：只有「直接 python main.py」时才执行；被 import 时不跑；类似 Java `public static void main` 门闸。
- `from src.offer_agent_core import ...`：包路径从仓库根开始，需保证 `PYTHONPATH` 或从根目录运行。
- `def print_result(query: str, result) -> None`：`result` 未注解类型时，IDE 仍可推断；Kotlin 会写 `result: QAResult`。

视觉 RAG Agent 面试演示主程序。

这个入口做两件事：
1) 跑几条典型 query，展示端到端行为
2) 跑简化离线评测，展示 Recall@10 与 Accuracy

如果你不熟 Python，可以按“Java main 方法”来理解：
- 这里负责组装依赖（retriever/router/verifier/memory），创建 QAEngine
- 然后调用两个函数：演示 + 评测
"""

from __future__ import annotations

from src.offer_agent_core import QAEngine, accuracy, average, build_engine, recall_at_k


def print_result(query: str, result) -> None:
    """
    统一打印结果，便于你在面试中逐条讲链路。

    为什么要打印这些字段：
    - rewritten_query：解释 query rewrite 做了什么
    - branch：解释 Router 为什么这么路由
    - hits：解释检索命中了哪些页面（Top-k）
    - verified：解释 verifier 是否通过（以及是否触发 fallback）
    """
    print("=" * 80)
    print(f"问题: {query}")
    print(f"改写: {result.rewritten_query}")
    print(f"路由分支: {result.branch}")
    print(f"回答: {result.answer}")
    print(f"Verifier通过: {result.verified}")
    print("Top-k命中页:", [f"{h.page_id}({h.score:.3f})" for h in result.hits])
    if result.retry_hits:
        print("重试命中页:", [f"{h.page_id}({h.score:.3f})" for h in result.retry_hits])


def run_demo(engine: QAEngine) -> None:
    """
    演示问答（4 类典型问题各一条）。

    这 4 条分别覆盖你简历里的 4 个分支：
    - 图表：销售额最高
    - 表单：字段抽取（采购单号）
    - PPT：跨页/人物归因（谁负责介绍）
    - 翻译：外文故障码中文含义
    """
    demo_queries = [
        "2024Q3 经营分析里哪个产品线销售额最高？",
        "采购申请单的采购单号是多少？",
        "谁负责介绍了实验/试点安排？",
        "故障代码 E-203 的中文含义是什么？",
    ]
    for q in demo_queries:
        result = engine.ask(q)
        print_result(q, result)


def run_offline_eval(engine: QAEngine) -> None:
    """
    简化版离线评测（演示“持续回归”的骨架）。

    eval_set 每条样本包含：
    - query
    - gold_pages: 金标页面
    - gold_answer: 金标答案（为了演示，这里是简化文本）
    """
    eval_set = [
        {
            "query": "2024Q3 经营分析里哪个产品线销售额最高？",
            "gold_pages": {"report_p1"},
            "gold_answer": "产品线B（180）",
        },
        {
            "query": "采购申请单的采购单号是多少？",
            "gold_pages": {"form_p3"},
            "gold_answer": "PO-78421",
        },
        {
            "query": "谁负责介绍了实验/试点安排？",
            "gold_pages": {"ppt_p2"},
            "gold_answer": "张三",
        },
        {
            "query": "故障代码 E-203 的中文含义是什么？",
            "gold_pages": {"manual_en_p8"},
            "gold_answer": "[engine=gpt4o] Fault code E-203: 主轴温度过高, 需立即停机检修.",
        },
    ]

    recalls = []
    accs = []
    for sample in eval_set:
        # 对每条样本跑一次完整 pipeline，得到结果与中间轨迹
        result = engine.ask(sample["query"])

        # 检索指标：Recall@10
        ranked_page_ids = [h.page_id for h in result.hits]
        recalls.append(recall_at_k(ranked_page_ids, sample["gold_pages"], k=10))

        # 生成指标：Accuracy（demo 里用 exact match）
        accs.append(1.0 if accuracy(result.answer, sample["gold_answer"]) else 0.0)

    print("=" * 80)
    print("离线评测结果")
    print(f"Recall@10: {average(recalls):.3f}")
    print(f"Accuracy : {average(accs):.3f}")


def main() -> None:
    """
    程序入口（依赖组装）。

    生产系统里，这一步通常由 DI 框架完成（Java 的 Spring / Python 的依赖注入容器），
    demo 里我们手动 new 出来，方便你理解每个模块的职责边界。
    """
    engine = build_engine("data/demo_pages.json")
    run_demo(engine)
    run_offline_eval(engine)


if __name__ == "__main__":
    main()
