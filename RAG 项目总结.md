# RAG / 视觉 Agent 项目总结（对话与简历共用）

> **文档用途**：概括本项目「要做什么、怎么做、哪些已落地、哪些依赖云端/配置」。新开对话或复习面试时，可先让助手阅读本文，再提问，减少重复背景说明。  
> **更新说明**：2026-05 整理；技术细节与模块对齐见 `PDF功能接入完成度.md`。需求母版：`RAG 项目完整介绍 简历包装.pdf`（若有 `.cursor/pdf_extract/*.txt` 为抽取版便于检索）。

---

## 〇·一、三份材料各自是什么（关系别绕）

| 材料 | 角色 | 你怎么用它 |
|------|------|------------|
| **《简历包装》PDF** | **需求 / 叙事母版**：背景、七类数据、指标、技术栈、伪代码 | 讲业务目标、分层、数字时跟它走；**不等于**仓库内每一依赖都已开箱即用 |
| **本仓库代码** | **实现 + 你在 PDF 外的扩展**（增量建库、脚本、`/capabilities`、注释等） | 演示、举证、指到具体文件时用仓库 |
| **本文** | **PDF × 仓库的对齐说明**：一致处、缺口处、面试怎么说「不穿帮」 | 新开对话、复习、让 AI 同步上下文 |

**一句话**：PDF = 要什么、数字从哪来；仓库 = 你能跑什么、代码在哪；本文 = 两边怎么对上。

---

## 〇·二、PDF 需求 vs 当前仓库（摘要对照）

下表概括 PDF《完整介绍》中的要点与本开源仓库的关系（模块级细表见 `PDF功能接入完成度.md`）。

| PDF 中的能力 / 叙述 | 仓库侧状态 |
|----------------------|------------|
| 页图 embedding（ColPali / MiniCPM-V）→ Milvus | **接口已接**；满血需 **`RAG_MULTIMODAL_EMBEDDING_API`** + Milvus 等配置 |
| ColPali rerank | **接口已接**；参考 **`scripts/colpali_rerank_service.py`**；`RAG_COLPALI_RERANK_API` 指向 **本机或内网 GPU 节点**（与面试「本地显卡」口径一致） |
| Router：function calling、四工具 | **已实现**；模型由 **`OPENAI_*`** 指定，不限死 GPT-4o-mini |
| VLM / chart / translate 分支 | **工具链已实现**；**`RAG_VLM_API` / `RAG_CHART_PARSING_API` / 翻译 Key** 未配则降级 |
| Verifier、扩 top-k、外层 Loop | **已实现** |
| 七类数据、Recall@10、Accuracy、Router 92% 等 | **PDF 业务评测口径**；仓库有 **评测模块 + 小样本**，无完整十万页金标 |
| GPT-4o **批量造评测集**、Query 过滤、翻译 benchmark 流程 | **方法论在 PDF**；仓库 **未内置完整自动化造数流水线**（可自行接脚本） |
| 技术栈 **LangGraph** | 业界常用 **有向图状态机** 编排 Agent（节点=步骤、边=条件）。本仓库 **未引入 LangGraph**，用 **`pipeline.py` 固定主链 + `agent_loop.py` 外层重试** 达到等价的「可控编排」，简历可不写 LangGraph；被问到如实答即可。 |
| 技术栈 **vLLM** | **高吞吐 LLM 推理服务引擎**（PagedAttention、批处理），常把权重部署在 **GPU 上**，对外提供 **OpenAI 兼容 HTTP**。本仓库对话侧走 **OpenAI 兼容客户端**；推理进程可用 vLLM 托管，与是否在本仓库 import vLLM 无关。 |
| **PPOCR（PaddleOCR）** | PDF 中 **TextRAG / OCR 基线链路**：检测+识别+框合并 → 文本嵌入 → 生成；用于与「页图直编」做 **耗时与 Recall 对比**（如 312ms vs 121ms）。主线交付为 **页图 + ColPali 路线**；扫描件等弱版式场景可 **叠加 PPOCR 作辅助文本通道**（工程上常见，本开源仓库默认以 PyMuPDF 抽字为主）。 |

**结论**：PDF 描述 **企业目标架构与实验结论**；仓库是 **同一套分层设计的工程实现 + 可降级运行**。被深挖依赖库时，按上表区分「叙事栈」与「本仓库实际 import」。

---

## 〇、面试口径（固定）

- **视觉检索与重排的核心模型口径**：以 **ColPali** 作为页面图像检索 / late-interaction 重排的代表能力（与简历、PDF 一致）。
- **部署形态（你已定的说法）**：**推理部署在带 NVIDIA GPU 的本地工作站 / 机房机器上**（非笔记本小卡硬扛）；编排与 API 网关可在开发机或同网段服务器。工程上 **ColPali / VLM / embedding 等重推理** 以 **独立进程 + HTTP** 暴露，主服务通过环境变量里的 **本机或内网 URL** 调用（与仓库 `services.py` 形态一致：地址写成 `http://127.0.0.1:...` 或 `http://内网IP:...` 即「本地显卡」叙事）。
- **代码对应**：页图向量 → `RAG_MULTIMODAL_EMBEDDING_API`；候选页精排 → `RAG_COLPALI_RERANK_API`（参考 `scripts/colpali_rerank_service.py`）；对话模型可经 **vLLM 等** 提供 OpenAI 兼容接口，由 `OPENAI_BASE_URL` 指向该内网地址。
- **表述底线（避免「只看过没做过」）**：面试只讲 **你亲自写/联调/验收过** 的模块（建库、检索策略、Router、工具链、Verifier、评测脚本、部署与压测）；对 **同事维护的推理镜像、现成 OCR 基线脚本** 用 **「我负责对接与集成，按接口联调与线上观测」**，不把别人的工作说成自己从零训练模型。

---

## 〇·三、ReAct 与 Plan-and-Execute（面试必答，背这 6 句）

**ReAct（Reason + Act）**  
- 模型 **交错输出**：想一步 → **调工具** → 看工具返回 → 再想下一步……循环直到能答。  
- 适合 **工具多、中间结果不确定** 的探索型任务；缺点是若每步都自由发挥，**链路难控、成本高**。

**Plan-and-Execute（先计划再执行）**  
- 先把任务拆成 **固定或可变的阶段**（如：检索 → 选分支 → 生成 → 校验），再按阶段执行；失败时 **在同一套阶段上重试或扩大检索**（replan 可简可繁）。  
- 适合 **企业 RAG**：要 **可观测、可回放、SLA 可控**，不能每一步都让模型「即兴发挥」。

**本项目怎么落在代码上（避免只会背定义）**  
1. **内层**：`pipeline.py` 的 `QAEngine.ask` 是 **固定顺序的「小计划」**：检索 → Router → 工具生成 → Verifier → 未通过则 **扩 top-k 再检索再生成**（同一套阶段重复）。  
2. **外层**：`agent_loop.py` 在 **整轮仍不通过** 时 **加大 top-k 再 ask**，属于 **Observe（看 verified）→ Retry（换参重来）**，是 Plan-and-Execute 里常见的 **执行—反馈—再规划（这里规划=调参）** 的裁剪实现。  
3. **和 ReAct 的关系**：Router **选工具**、Verifier **给负反馈** 再触发动作，是 ReAct 里 **「Act + Observation」** 思想的工程化；但整体 **不采用**「每一步都让 LLM 自由决定下一步工具」的纯 ReAct，而是 **阶段写死 + 少量分支**，保证 **线上可控**。  
4. **一句话收口**：**「编排哲学上吸收 ReAct 的反馈闭环，工程形态上采用 Plan-and-Execute 的分段流水线 + 外层重试。」**

---

## 〇·四、三个名词各一句（被问到再展开）

| 名词 | 一句话 |
|------|--------|
| **vLLM** | 把大模型部署在 GPU 上、提供 **高并发 OpenAI 兼容 HTTP** 的推理服务；我们业务 API 只调 URL，不关心进程里是 vLLM 还是别的。 |
| **LangGraph** | LangChain 生态里用 **有向图** 编 Agent 状态的库；**本项目未使用**；用 **手写 pipeline + agent_loop** 达到类似「可观测多步」的效果。简历 **可不写** LangGraph。 |
| **PPOCR** | **PaddleOCR**，传统 **检测+识别** 出全文再走 TextRAG 的基线；与 **页图 + ColPali** 路线对比（PDF 里 312ms vs 121ms）；弱扫描件可 **并行一条 OCR 文本** 辅助检索，主交付仍是视觉索引。 |

---

## 一、项目概述

| 项目 | 说明 |
|------|------|
| **名称** | 多模态知识库：基于页面图像的视觉 RAG + Agent 问答 |
| **场景** | 企业内部 PDF、PPT、报表、合同、手册等图文混排文档问答 |
| **痛点** | 纯 OCR + TextRAG 误差易传导；版式与图表信息在「只转纯文本」时丢失 |
| **思路** | 以 **页** 为单元：页图参与检索（ColPali 路线）+ 文本与结构化字段辅助；**Router** 按问题类型走不同工具链；**Verifier** 做可证性校验与失败重试 |

---

## 二、核心挑战与方案（精简）

| 挑战 | 方向 |
|------|------|
| OCR/版面误差传导 | 页级视觉检索为主；**PPOCR/TextRAG 作基线对照与弱版式辅助**；Verifier 压幻觉 |
| 图表、版式信息丢失 | 页图编码进检索；图表分支走 `chart_qa` + 结构化 `chart_data` |
| top-k 噪声 | query 改写、类型预过滤、**ColPali rerank**、扩 top-k 重试 |
| 查询类型多样 | 四分支：`fact_qa` / `multi_page_qa` / `chart_qa` / `translate_qa` |
| 跨语种 | `translate_qa` 多引擎候选与选优（依赖配置翻译 API） |

---

## 三、分层架构（与代码模块对齐）

| 层级 | 能力 | 工程位置（概要） |
|------|------|------------------|
| **L0 检索** | 页面向量、混合词面、query rewrite、类型预过滤、可选 Milvus、可选 ColPali rerank | `retriever.py`、`infra/vector_store.py`、`services.py` |
| **L1 路由** | 规则 / LLM function calling 选分支 | `router.py`、`llm_client.py` |
| **L2 生成** | 四工具 + 可选 VLM / chart HTTP | `tools.py`、`pipeline.py` |
| **校验与记忆** | Verifier、扩召回重试、Session（可选 Redis） | `verifier.py`、`pipeline.py`、`agent_loop.py`、`memory.py` |
| **服务化** | FastAPI、`/metrics`、静态聊天页 | `api.py` |

---

## 四、功能完成度（心里要有数）

### 1）已在开源仓库中 **模块就绪** 的能力

- 端到端链路：检索 → 路由 → 工具生成 → Verifier → 重试 / 外层 Agent Loop（可开关）。
- PDF/多格式 **增量建库**、页数据 JSON、`pdf_ingest` 页图渲染骨架。
- ColPali **本地参考服务**：`scripts/colpali_rerank_service.py`（重排）；模型目录约定 `models/colpali-v1.3`。
- **HTTP 适配层**：多模态 embedding、ColPali rerank、VLM、chart-parsing（`services.py`），便于 **本机 / 内网 GPU 节点** 与主 API **进程隔离**。
- Milvus / Redis / Prometheus 等 **可选后端**与降级路径。

### 2）依赖 **环境变量与远端服务** 才「满血」的能力

- **ColPali 页图编码入库**：需配置 `RAG_MULTIMODAL_EMBEDDING_API` 指向 **GPU 上实际跑起来的编码服务**（常为内网地址）。
- **ColPali 重排**：需配置 `RAG_COLPALI_RERANK_API`（如本机 `127.0.0.1` 或内网 GPU 机上的 `/rerank`）。
- **VLM 看图问答 / 视觉校验**：需 `RAG_VLM_API`。
- **图表解析服务**：需 `RAG_CHART_PARSING_API`（否则依赖 JSON 内 `chart_data` 等）。
- **真实文本 embedding**：`RAG_ENABLE_REAL_EMBEDDING` + 兼容 OpenAI 的 embedding 接口。
- **翻译多引擎**：Google / DeepL / 兼容 Chat 等 **Key 与 URL**。

未配置时，工程 **刻意** 走哈希向量、规则、纯文本等 **降级路径**，保证仓库可跑通演示；**与「企业满配」是两档**。面试可说：**「生产上推理在 GPU 工作站；本仓库开源版保留降级便于协作。」**

### 3）文档中的 **企业级数据与指标**（与开源仓库关系）

- 七类文档、**13.5 万页 / 23.9 万页**、Recall@10、Accuracy、Router 92%、翻译 domain 等数字 —— 来自 **业务阶段评测与 PDF 目标**；开源仓库内 **具备评测脚本与小样本验证**，**不具备**完整十万页语料与金标集。
- 面试表述建议：**「指标来自当时企业侧评测集；当前开源仓库复现的是模块与流程，完整数据在内网。」**

---

## 五、数据与评测（业务叙事）

### 1）七类内部文档任务

业务图表与报表、图表专项页、合同与工业表单、信息图与宣传页、数据看板与曲线、培训与汇报 PPT（含跨页）、跨语种手册等。

### 2）数据规模（叙述口径）

- 七类场景索引规模合计约 **13.5 万页**（按业务侧统计口径）。  
- 合成扩充约 **23.9 万页**（评测补充与 RAG eval）。

### 3）评测指标类型

Recall@10、Accuracy、Router 决策准确率、翻译引擎选择准确率等；具体数值见第六节。

---

## 六、关键实验结果（业务/PDF 口径；非本仓库一键复现）

下列数字用于 **对内汇报与简历**，与当前 Git 默认开箱数据 **不要求一致**：

- **检索**：最终方案 Recall@10 **89.40**（相对 ColPali 基线 **87.62** 约 **+1.8**）。
- **生成**：Router 四分支 Accuracy **58.70**；对比 TextRAG+OCR 约 **46.27**。
- **翻译**：通用 domain **80.6**；公司术语 domain **70.4**（动态选优 vs 固定单引擎）。
- **子集**：信息图/宣传 TextRAG 约 **25%** → 页图 + Agent 约 **51%**。
- **效率**：离线单页约 **121ms**（页图编码口径）、在线检索约 **54ms**（目标环境）；translate_qa 多引擎并行约 **800ms**。

---

## 七、技术亮点（面试短句）

- **工程化检索**：离线渲染页图 → 向量入库；在线 rewrite + 类型预过滤 + **ColPali rerank**。  
- **Agent**：Router 分流 + Verifier + 扩 top-k / 换策略。  
- **数据闭环**：评测脚本与指标模块支持离线回归（`eval_metrics.py`、`eval_suite.py`）。  
- **部署**：重推理在 **本地 GPU 工作站**；主 API 通过 **HTTP / OpenAI 兼容** 调用；仓库侧负责 **编排、观测与降级**。

---

## 八、局限与下一步（诚实）

| 局限 | 说明 |
|------|------|
| 延迟 | translate_qa 多引擎并行带来额外耗时 |
| 长尾 | 复杂推理、强噪声页、细粒度图表仍难 |
| 复现 | 七类全量指标依赖原始数据与全部外部服务 |

建议方向：路由难例覆盖、翻译分支预判减并行、监控 verifier 失败类型、query 分层降成本。

---

## 九、一句话结论

项目采用 **「ColPali 为核心的页级视觉检索 +（可选）late-interaction 重排」**，配合 **Router 多分支、Verifier 与翻译选优**；推理部署在 **带 GPU 的本地/内网机器**，主服务经 **HTTP 与 OpenAI 兼容网关** 调用，实现 **算力与业务编排解耦**。

---

## 十、关联文件（ deep dive 时用）

| 文件 | 内容 |
|------|------|
| `RAG 项目完整介绍 简历包装.pdf` | 需求与简历叙事母版（原始）；抽取文本见 `.cursor/pdf_extract/`（若有） |
| `PDF功能接入完成度.md` | 功能点与模块逐条对齐、环境变量示例、精度说明 |
| `README.md` | 启动方式、模块列表 |
| `src/pipeline.py` | 主链路注释与简历映射 |

---

## 十一、本文是否「总结全面」？

- **覆盖 PDF 主线**：痛点、L0/L1/L2、四分支、Verifier、翻译与检索指标、ColPali 与工程增益叙事 —— **已覆盖**。  
- **刻意不单列的细节**：PDF 伪代码级示例等 —— 见 `PDF功能接入完成度.md`。  
- **已在本文展开的**：ReAct / Plan-and-Execute（〇·三）、vLLM / LangGraph / PPOCR（〇·四）。  
- **仓库多于 PDF 的部分**：增量建库、Lite 模式、`/capabilities`、Prometheus 等 —— 见 `README.md`。
