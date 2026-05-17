"""
tools.py — L2 生成：四条分支工具（fact / multi_page / chart / translate）

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
- 仅被 `pipeline.QAEngine._run_branch` 调用；输入已是「检索后的 Page 列表」。
- 每条工具内部再调 `services` 里的 HTTP Client（VLM / Chart / Translation）或 `LLMClient`；失败则 `_fact_fallback_formatted` 等规则兜底（你导出 JSON 里看到的「简要结论」模板）。

================================================================================
【类比 Android】
================================================================================
- 像 **UseCase 包**：`FactQaUseCase`、`TranslateQaUseCase` 各自封装下游 RPC；`ThreadPoolExecutor` 类似 `ExecutorService` 并行翻译引擎。
- `_doc_full_name`：从 `Page` 取展示文件名，类似从 `MediaItem` 取 `displayName`。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `dict.fromkeys(iterable)`：有序去重（Py3.7+ dict 保序），比手写 `LinkedHashSet` 模式短。
- `Optional[LLMClient] = None`：默认不注入 LLM；Kotlin 默认参数 `llm: LLMClient? = null`。
- `with ThreadPoolExecutor(...) as pool:`：上下文管理器，`with` 结束时自动 `shutdown`；类似 `use` / try-with-resources 模式封装线程池。
- `lines: List[str] = []` 后 `lines.append`：可变列表累加字符串块，最后 `"\n".join(lines)`。

四条分支工具实现（简化版）。

设计目标：
1) 与真实项目的工具形态一致
2) 行为可解释，方便面试演示
3) 注释足够详细，帮助你理解关键词

你可以把 tools.py 当成“下游能力集合”：
- 在真实系统里，这些可能是：
  - 单图/多图 VLM 推理服务（HTTP/RPC）
  - 图表解析服务（chart-parsing）
  - 翻译服务（Google/DeepL/LLM）
- 在这个 demo 里，我们用“结构化字段/规则/并行”来模拟这些能力的接口形态
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional

from .config import SETTINGS
from .llm_client import LLMClient
from .models import Page
from .services import ChartParsingClient, TranslationEngineClient, VLMClient

logger = logging.getLogger(__name__)


def _best_matching_token(query: str, candidates: List[str]) -> str:
    """
    从候选项中找最可能与 query 匹配的关键词（非常简化的匹配器）。

    为什么需要这个函数：
    - 在多页推理/图表问答里，我们经常需要从“候选实体集合”里挑一个最相关的
    - 真实系统会用更强的模型或更复杂的打分器，这里用可解释规则替代
    """
    q = query.lower()
    for c in candidates:
        if c.lower() in q:
            return c
    return candidates[0] if candidates else ""


def _doc_full_name(page: Page) -> str:
    if page.source_file:
        return Path(page.source_file).name
    meta = page.metadata or {}
    for key in ("source_filename", "original_filename", "file_name", "filename"):
        if meta.get(key):
            return Path(str(meta[key])).name
    return page.doc_id


def _fact_fallback_formatted(query: str, pages: List[Page]) -> str:
    """
    LLM / VLM 不可用时的兜底：固定分段排版，避免把 raw chunk 糊成一整坨。
    """
    doc_names = list(dict.fromkeys(_doc_full_name(p) for p in pages))
    lines: List[str] = []
    qshort = (query or "").strip()
    if len(qshort) > 240:
        qshort = qshort[:240] + "…"
    lines.append("【当前结果】")
    lines.append(f"问题：{qshort or '（空）'}")
    lines.append("暂未生成稳定归纳答案，先返回命中证据供你核对。")
    lines.append("")
    lines.append("【命中文档】")
    for n in doc_names:
        lines.append(f"- {n}")
    lines.append("")
    lines.append("【关键摘录（最多2条）】")
    for i, p in enumerate(pages[:2], 1):
        fname = _doc_full_name(p)
        meta_title = (p.metadata or {}).get("title") or ""
        label = meta_title.strip() or p.page_id
        raw = (p.content or "").strip()
        excerpt = " ".join(raw.split())[:220]
        if len(raw) > 220:
            excerpt += "…"
        lines.append(f"{i}. 《{fname}》｜{label}")
        lines.append(f"   {excerpt}")
        lines.append("")
    lines.append("【下一步建议】")
    lines.append("请把问题改得更具体（字段名/章节名/时间范围），或确认 OPENAI_API_KEY / OPENAI_BASE_URL 可用。")
    return "\n".join(lines).strip()


def fact_qa(query: str, pages: List[Page], llm: Optional[LLMClient] = None) -> str:
    """
    事实问答：可合并多页（同一 Excel 多 sheet、或 top-k 多段材料）。

    优先 VLM（仅对带图的首个候选尝试），其次 LLM 归纳；单页且带结构化字段时才走字段捷径。
    """
    if not pages:
        return "没有检索到候选页面。"

    dedup: List[Page] = []
    seen: set = set()
    for p in pages:
        if p.page_id not in seen:
            seen.add(p.page_id)
            dedup.append(p)
    pages = dedup

    vlm = VLMClient()
    for page in pages:
        if page.image_path and vlm.enabled:
            try:
                answer = vlm.answer(query=query, image_paths=[page.image_path], mode="single_page")
                if answer:
                    return answer
            except Exception:
                break

    if llm and llm.enabled:
        try:
            blocks: List[str] = []
            for page in pages:
                title = (page.metadata or {}).get("title") or ""
                fname = _doc_full_name(page)
                head = f"【文档全名】{fname} ｜ page_id={page.page_id}"
                if title:
                    head += f" ｜ {title}"
                if page.fields:
                    blocks.append(head + "\n结构化字段（JSON）：\n" + json.dumps(page.fields, ensure_ascii=False))
                body = (page.content or "").strip()
                if body:
                    blocks.append(head + "\n页面正文：\n" + body[:9000])
            ctx = "\n\n---\n\n".join(blocks)
            if ctx:
                synthesized = llm.chat_text(
                    "你是企业知识库问答助手。只能依据「材料」段落作答，不要使用外部常识臆测。"
                    "输出必须使用清晰层级：先写「依据文档：」列出完整文件名（含扩展名）；再写「结论」用 2～5 条要点回答用户问题；"
                    "需要时可写「摘录」短引用。不要使用一整段无标题的长代码块堆砌。"
                    "若材料不足，写「材料中未找到足够依据」并说明缺口。"
                    "回答正文不少于 120 字为宜（除非材料本身极短）。",
                    f"用户问题：{query}\n\n材料：\n{ctx}",
                )
                if synthesized:
                    return synthesized
        except Exception as exc:
            logger.warning("fact_qa llm synthesis failed, fallback to formatted snippets: %s", exc)

    if len(pages) == 1 and pages[0].fields:
        page = pages[0]
        for key in ["采购单号", "发票日期", "负责人"]:
            if key in page.fields:
                return f"依据文档：{_doc_full_name(page)}\n{page.fields[key]}"
        first_key = next(iter(page.fields))
        return f"依据文档：{_doc_full_name(page)}\n{page.fields[first_key]}"

    return _fact_fallback_formatted(query, pages)


def multi_page_qa(query: str, pages: List[Page], llm: Optional[LLMClient] = None) -> str:
    """
    多页推理。

    这里简化成：从多页 people 列表中找最可能的人名。
    真实系统会使用多图 VLM 进行跨页关联推理。
    """
    image_paths = [p.image_path for p in pages if p.image_path]
    vlm = VLMClient()
    if image_paths and vlm.enabled:
        try:
            answer = vlm.answer(query=query, image_paths=image_paths[:3], mode="multi_page")
            if answer:
                return answer
        except Exception as exc:
            logger.warning("multi_page_qa llm synthesis failed, fallback to rule mode: %s", exc)

    if llm and llm.enabled and pages:
        try:
            blocks = []
            for p in pages:
                fname = _doc_full_name(p)
                title = (p.metadata or {}).get("title") or ""
                head = f"【文档全名】{fname} ｜ page_id={p.page_id}"
                if title:
                    head += f" ｜ sheet/标题：{title}"
                chunk = (p.content or "").strip()[:5000]
                if chunk:
                    blocks.append(f"{head}\n{chunk}")
            ctx = "\n\n---\n\n".join(blocks)[:15000]
            if ctx:
                synthesized = llm.chat_text(
                    "你是企业知识库助手，需要综合多段页面文字回答问题。只能使用给定材料，不要臆测。"
                    "第一段必须以「依据文档：」开头，列出所有用到的完整文件名（含扩展名），多个用顿号「、」分隔。"
                    "后续分段、有层次，尽量写充分；若跨页信息仍不足，请说明缺口。"
                    "如信息来自不同 sheet，可在句末标注对应 sheet 或 page_id。",
                    f"用户问题：{query}\n\n候选页面：\n{ctx}",
                )
                if synthesized:
                    return synthesized
        except Exception:
            pass

    people = []
    for p in pages:
        people.extend(p.people)
    people = list(dict.fromkeys(people))  # 去重并保序
    if not people:
        return "未在候选多页中发现明确人名信息。"
    return _best_matching_token(query, people)


def chart_qa(query: str, pages: List[Page]) -> str:
    """
    图表分支。

    思路（真实系统里通常是“图表解析 -> 数值校验 -> 归一化输出”）：
    - 汇总候选页里的 chart_data（例如 A:120, B:180）
    - 若 query 包含“最高/最大”，返回最大值对应项
    - 否则返回最相关项或默认项
    """
    image_paths = [p.image_path for p in pages if p.image_path]
    parser = ChartParsingClient()
    merged: Dict[str, float] = {}
    if image_paths and parser.enabled:
        try:
            merged.update(parser.parse(query=query, image_paths=image_paths[:2]))
        except Exception:
            pass

    for p in pages:
        for k, v in p.chart_data.items():
            merged[k] = max(v, merged.get(k, float("-inf")))

    if not merged:
        return "当前候选页缺少图表结构化数据，无法稳定读数。"

    names = "、".join(dict.fromkeys(_doc_full_name(p) for p in pages if p.source_file))
    prefix = f"依据文档：{names}\n\n" if names else ""

    q = query.lower()
    if any(x in q for x in ["最高", "最大", "top", "best"]):
        best_name = max(merged, key=merged.get)
        return prefix + f"{best_name}（{merged[best_name]}）"

    pick = _best_matching_token(query, list(merged.keys()))
    return prefix + f"{pick}（{merged[pick]}）"


def _translate_google(src: str) -> str:
    """模拟 Google 翻译输出。"""
    return src.replace("Spindle temperature too high", "主轴温度过高").replace("please stop the machine", "请停机检修")


def _translate_deepl(src: str) -> str:
    """模拟 DeepL 翻译输出。"""
    return src.replace("Spindle temperature too high", "主轴温度偏高").replace("please stop the machine", "请停止机器")


def _translate_gpt4o(src: str) -> str:
    """模拟 GPT-4o 翻译输出（术语更贴近业务）。"""
    return src.replace("Spindle temperature too high", "主轴温度过高").replace("please stop the machine", "需立即停机检修")


def _score_translation(text: str) -> float:
    """
    翻译质量打分（简化规则）。

    面试可讲：真实工程中通常是“规则 + 模型”混合评分。
    - 规则：领域术语命中、关键短语覆盖、长度惩罚等
    - 模型：用 LLM/打分模型对候选翻译做质量评分
    """
    score = 0.0
    if "主轴" in text:
        score += 0.4
    if "停机" in text:
        score += 0.3
    if "检修" in text:
        score += 0.3
    if "立即" in text or "需" in text:
        score += 0.1
    return score


def translate_qa(query: str, page: Page, llm_client: Optional[LLMClient] = None) -> str:
    """
    翻译分支。

    真实逻辑（与你简历一致）：
    - OCR 抽取外文原文
    - 并行调用多个翻译引擎
    - scorer 选优
    - 返回最佳译文
    """
    src = page.content
    if SETTINGS.enable_llm_translation and llm_client and llm_client.enabled:
        try:
            translated = llm_client.chat_text(
                system_prompt="你是工业手册翻译助手。保留故障码，输出自然、准确、术语一致的中文。",
                user_prompt=f"问题：{query}\n原文：{src}\n请只输出中文答案。",
            )
            if translated:
                return f"[engine=llm] {translated}"
        except Exception:
            pass

    engines = {
        "google": _translate_google,
        "deepl": _translate_deepl,
        "gpt4o": _translate_gpt4o,
    }
    external = TranslationEngineClient()
    external_candidates: Dict[str, str] = {}
    try:
        google_result = external.google(src)
        if google_result:
            external_candidates["google"] = google_result
    except Exception:
        pass
    try:
        deepl_result = external.deepl(src)
        if deepl_result:
            external_candidates["deepl"] = deepl_result
    except Exception:
        pass
    try:
        oapi_result = external.oapi_chat(src)
        if oapi_result:
            external_candidates["oapi"] = oapi_result
    except Exception:
        pass

    # 并行调用多个引擎（你可以把它当成：并行 RPC 调用多个下游服务）
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {name: pool.submit(fn, src) for name, fn in engines.items()}
        candidates = {name: fut.result() for name, fut in futures.items()}
    candidates.update(external_candidates)

    # 选优：对每个候选翻译打分，选分数最高的
    best_engine = max(candidates.keys(), key=lambda e: _score_translation(candidates[e]))

    # 演示版暂不使用 query 做二次约束（真实系统可以用 query 引导评分，例如“要求输出中文含义/字段抽取”）
    _ = query
    return f"[engine={best_engine}] {candidates[best_engine]}"
