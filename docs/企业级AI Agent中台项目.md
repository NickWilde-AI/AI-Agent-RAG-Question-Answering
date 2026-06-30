# 企业级 AI Agent 中台项目

> 本文档参照 `private/多模态知识库问答RAG+Agent项目.md` 与 `private/多模态知识库问答RAG+Agent项目_QA版.md` 的写法整理。
> 定位：把当前已经实现的“企业多模态 RAG+Agent”升级描述为“企业级 AI Agent 中台”的首个可落地版本。
> 核心原则：**四个业务功能复用同一套多模态 RAG+Agent 底座，不做四套孤立系统。**

---

## 1. 项目概述与定位

### 1.1 项目背景

企业内部存在大量非结构化和半结构化资料，包括 PDF、PPT、Word、Excel、经营报表、合同、发票、简历、岗位 JD、项目手册和技术说明书。传统企业搜索通常只能做关键词匹配，传统 TextRAG 又高度依赖 OCR 和文本切块，对图表、PPT、扫描件、复杂版式和跨页推理支持不足。

本项目最初实现的是一个企业内部多模态 RAG+Agent 问答系统，已经具备页面级建库、稳定 `page_id`、增量重建、三分支 Router、VLM 页图推理、Verifier 校验、Fallback、Workspace 隔离、SSE Trace、评测和金标工具等主体能力。

在此基础上，项目升级为 **企业级 AI Agent 中台**：以当前多模态 RAG+Agent 为第一个真实可用的 **RAG Skill**，向企业知识问答、企业报表分析、合同/表单/发票抽取、HR 招聘辅助四类高频企业场景扩展。

### 1.2 项目目标

项目目标不是做一个“万能聊天机器人”，而是把企业文档理解、检索、视觉问答、结构化抽取、证据校验和执行轨迹统一沉淀为可复用的 Agent 中台能力。

核心目标：

- 将现有多模态 RAG 能力沉淀为 `RAG Skill`。
- 将企业常见任务抽象为可复用的业务 Skill。
- 通过 Router 自动识别用户任务类型。
- 通过 VLM/LLM 完成页面理解、图表分析、字段抽取和总结生成。
- 通过 Verifier 校验答案是否可由证据页支撑。
- 通过 Trace 展示检索、路由、生成、校验全过程。
- 通过评测体系持续验证效果。

### 1.3 当前聚焦的四个业务功能

本阶段只聚焦四个功能，不加入会议纪要和机器人控制。

| 功能 | 说明 | 当前基础 |
|---|---|---|
| 企业知识库问答 | 面向制度、手册、PPT、项目文档、产品资料的多模态问答 | 已实现主体能力 |
| 企业报表分析 | 面向 Excel、经营报表、PPT 图表、数据看板的数值问答和趋势解释 | 已有 `chart_qa`、VLM 页图理解基础 |
| 合同/表单/发票抽取 | 面向合同、采购单、验收单、发票、报销单的字段抽取和证据定位 | 可复用 VLM/RAG/结构化抽取链路 |
| HR 招聘辅助 | 面向简历、JD、候选人资料的匹配分析、亮点风险总结和面试题生成 | 可复用文档理解、多文档对比和结构化输出 |

---

## 2. 为什么从 RAG 升级为 Agent 中台

### 2.1 传统 RAG 的问题

传统 RAG 通常是：

```text
文档解析 → 文本切块 → 向量检索 → LLM 生成
```

这条链路在纯文本文档中可用，但在企业真实文档中会遇到明显问题：

- PPT、报表、扫描件、合同表单大量依赖版式和视觉信息。
- OCR 错误会传递到检索和生成环节。
- 图表和表格中的数值、坐标轴、图例关系难以通过普通文本切块表达。
- 用户问题类型差异很大，单一 pipeline 很难同时处理事实问答、图表计算、跨页推理和字段抽取。
- 答案如果没有证据页和校验机制，容易出现“看起来正确但无法溯源”的幻觉。

### 2.2 Agent 中台的升级点

升级后的中台不是简单多加几个 Prompt，而是将系统拆成可治理的执行链路：

```text
用户任务
  → Intent / Router
  → 选择业务 Skill
  → 检索与页面理解
  → VLM/LLM 生成或结构化抽取
  → Verifier 证据校验
  → 结构化结果 + 证据页 + Trace
```

相比普通 RAG，Agent 中台新增的价值包括：

- **任务分流**：不同问题走不同 Skill。
- **多模态理解**：直接理解页面图像、图表、扫描件。
- **结构化输出**：支持 JSON 字段、表格、评分矩阵。
- **证据校验**：答案必须能回到页面证据。
- **运行可观测**：展示检索、路由、生成、校验全过程。
- **复用能力**：四个业务功能共用底层能力。

---

## 3. 统一技术底座

四个业务功能不是四套系统，而是共用同一套多模态 RAG+Agent 底座。

### 3.1 共用链路

```text
文档上传 / 资料接入
  → 文档解析与页面级建库
  → page_id 稳定映射
  → BM25 / Hybrid / 可选向量检索
  → Query Rewrite
  → Router 判断任务类型
  → 选择业务 Skill
  → VLM / LLM 页面理解与生成
  → Verifier 证据校验
  → 结构化输出 + 证据页 + Trace
  → 评测与错误回放
```

### 3.2 共用能力说明

| 共用能力 | 作用 | 复用到哪些功能 |
|---|---|---|
| 文档上传与解析 | 支持 PDF、PPT、Word、Excel、图片、扫描件 | 四个功能全部复用 |
| 页面级建库 | 以页面为最小证据单元，生成稳定 `page_id` | 知识库、报表、合同、HR |
| 多模态检索 | 支持文本、图表、截图、表格、扫描件检索 | 知识库、报表、合同 |
| Query Rewrite | 改写口语化问题，增强召回 | 四个功能全部复用 |
| Router | 判断任务属于知识问答、报表分析、字段抽取还是 HR 匹配 | 四个功能全部复用 |
| VLM 页图理解 | 直接理解图表、发票、合同扫描件、简历版式 | 报表、合同、HR、复杂知识问答 |
| LLM 生成 | 生成自然语言答案、总结、建议、面试题 | 四个功能全部复用 |
| 结构化抽取 | 输出 JSON、表格、评分矩阵、字段结果 | 报表、合同、HR |
| Verifier | 判断答案是否有页面证据支撑 | 四个功能全部复用 |
| Trace | 展示路由、检索、生成、校验、耗时 | 四个功能全部复用 |
| Golden Eval | 用金标集评估检索、生成、路由和校验 | 四个功能全部复用 |

### 3.3 当前仓库已有基础

根据实现度审计，当前仓库已经具备以下主体能力：

- 页面级文档入库。
- 稳定 ID、增量重建和断点续跑。
- `fact_qa` / `multi_page_qa` / `chart_qa` 三分支 Router。
- Query Rewrite。
- 文档类型预过滤。
- 混合检索和 BM25 降级。
- 可选 Milvus、多模态 Embedding、ColPali 接口。
- VLM 页图推理 adapter。
- Verifier、有限重试和拒答。
- Session QA 缓存。
- Workspace 隔离。
- 研究任务和报告。
- SSE 执行轨迹。
- 离线评测、金标集和批量报告。
- Kafka 增量入口、夜间重建脚本、监控和灰度配置骨架。

需要注意：导师文档中的百万页规模、MRR、延迟和并发数字属于目标架构或历史叙事口径，当前公开仓库需要在目标环境重新压测和评测后才能对外声称。

---

## 4. 功能一：企业知识库问答

### 4.1 功能定位

企业知识库问答是当前项目最成熟的能力，也是整个 Agent 中台的第一个真实 Skill。

它面向企业内部各种知识资料：

- 产品手册
- 项目文档
- 需求文档
- 培训资料
- PPT 汇报
- 规格说明书
- FAQ
- 制度文档
- 技术白皮书

### 4.2 用户能做什么

用户可以直接上传或选择企业资料，然后用自然语言提问：

- “这个项目的交付时间是什么？”
- “这个系统架构包含哪些模块？”
- “某个接口参数是什么意思？”
- “第 8 页提到的 347+ 量产平台项目是什么意思？”
- “这个规格说明书里 WMPF 的限制是什么？”
- “这个问题的根本原因是什么？”

系统返回：

- 直接结论。
- 证据页。
- 命中文档。
- 关键摘录。
- 检索与运行详情。
- 如果证据不足，明确拒答或提示用户缩小问题范围。

### 4.3 技术细节

企业知识库问答复用当前 RAG+Agent 主体链路：

- 页面级 chunk，而不是纯文本 chunk。
- 稳定 `page_id`。
- BM25 / Hybrid Search。
- Query Rewrite。
- Top-K 检索。
- VLM 页图推理。
- 三分支 Router：`fact_qa` / `multi_page_qa` / `chart_qa`。
- Verifier 证据校验。
- SSE Trace 展示执行过程。
- 离线评测和金标集。

### 4.4 与普通知识库的区别

普通企业知识库通常只能回答“文本里写了什么”。本项目更强调：

- 图表页可问答。
- PPT 跨页可聚合。
- 扫描件和复杂版式可理解。
- 答案有证据页。
- 运行过程可追踪。
- 可通过评测集持续验证。

---

## 5. 功能二：企业报表分析

### 5.1 功能定位

企业报表分析面向经营报表、Excel、PPT 图表页、月报、周报、KPI 看板和趋势图。它不是完整 BI 系统，而是“文档型报表问答”和“图表页分析”。

### 5.2 用户能做什么

用户可以提问：

- “2024 年哪个产品线增长最快？”
- “海外员工占比是多少？”
- “主机厂数量比一级供应商多多少？”
- “这个月销售额下降主要体现在哪个区域？”
- “这个图表反映了什么趋势？”
- “如果每个平台平均对应 2 款车型，大约覆盖多少车型？”

系统返回：

- 数值答案。
- 计算过程。
- 趋势解释。
- 图表依据页。
- 单位和口径说明。
- 证据不足时拒答。

### 5.3 技术细节

企业报表分析主要复用 `chart_qa` 和 VLM 页图理解：

- 图表类型识别：柱状图、折线图、饼图、表格、KPI 大屏。
- 坐标轴、图例、单位识别。
- 数值抽取。
- 简单计算。
- Relaxed Exact Match 评测。
- 数值合理性校验。
- 多页报表上下文聚合。
- Verifier 对数值和证据进行校验。

示例：

```text
用户问：海外员工占比是多少？

系统识别：
- 员工总数：17000+
- 海外员工：3000+

计算：
3000 / 17000 ≈ 17.6%

输出：
海外员工占比约为 17.6%，依据来自命中页。
```

### 5.4 可复用价值

报表分析沉淀出的能力可以复用到：

- 发票金额识别。
- 合同金额抽取。
- HR 候选人年限统计。
- 表格字段抽取。
- 图表型企业知识问答。

---

## 6. 功能三：合同 / 表单 / 发票抽取

### 6.1 功能定位

合同、表单、发票、采购单、验收单和报销单属于企业内部高频的半结构化文档。它们通常版式固定但字段密集，既需要视觉理解，也需要结构化抽取和证据定位。

### 6.2 用户能做什么

用户可以上传或选择文档，然后提问：

- “这张发票的金额是多少？”
- “这个合同的付款周期是什么？”
- “采购单号是多少？”
- “合同里有没有违约责任条款？”
- “这份报销单缺少哪些字段？”
- “验收日期是哪天？”

系统返回：

- 字段抽取结果。
- 自然语言解释。
- 证据页。
- 置信度或校验状态。
- 风险提示。
- 缺失字段说明。

### 6.3 技术细节

该功能复用现有文档理解和 VLM 页图推理能力，并在输出层增加结构化 schema：

- VLM / OCR 页面理解。
- Key-Value Extraction。
- Layout-aware parsing。
- JSON schema 输出。
- 字段置信度。
- 证据页定位。
- Verifier 字段校验。
- 敏感信息脱敏。
- 人工确认机制。

示例输出：

```json
{
  "invoice_title": "北京某某科技有限公司",
  "amount": "12800.00",
  "tax_id": "9111********",
  "invoice_date": "2026-06-20",
  "confidence": 0.92,
  "evidence_page": "doc_xxx_p3"
}
```

### 6.4 与普通 OCR 的区别

普通 OCR 只负责“识别文字”。本项目更强调：

- 能理解字段语义。
- 能定位字段来源。
- 能基于上下文判断缺失或异常。
- 能输出结构化 JSON。
- 能通过 Verifier 校验。
- 能复用 RAG 证据链。

---

## 7. 功能四：HR 招聘辅助

### 7.1 功能定位

HR 招聘辅助面向简历、岗位 JD、候选人作品说明和面试反馈。它不是替代 HR 决策，而是辅助 HR 和技术面试官快速理解候选人资料，生成结构化匹配报告。

### 7.2 用户能做什么

用户可以上传简历和 JD，然后提问：

- “这个候选人适合 AI Agent 工程师岗位吗？”
- “他的项目经历和 JD 匹配度怎么样？”
- “帮我总结他的 3 个亮点和 3 个风险点。”
- “根据这份简历生成 10 个面试问题。”
- “这个候选人有没有多模态 RAG 经验？”
- “他的后端经验和 Agent 项目经验是否匹配岗位要求？”

系统返回：

- 候选人摘要。
- 技能匹配表。
- 项目亮点。
- 风险点。
- 面试追问。
- 证据引用。
- 推荐等级或匹配度说明。

### 7.3 技术细节

HR 招聘辅助复用多文档理解、信息抽取和结构化生成能力：

- 简历文档解析。
- JD 文档解析。
- 技能实体识别。
- 项目经历归纳。
- RAG 对齐岗位要求。
- LLM 评分/排序。
- 结构化匹配矩阵。
- 证据引用。
- Prompt 模板。

示例匹配表：

| 维度 | 结果 | 证据 |
|---|---|---|
| Python 后端 | 匹配 | 简历中提到 FastAPI / Python 项目 |
| RAG 经验 | 强匹配 | 项目经历包含多模态 RAG |
| Agent 经验 | 中高匹配 | 有 Router / Verifier / MCP 设计 |
| 行业业务经验 | 中等匹配 | 有企业知识库和多模态文档项目经验，行业场景需继续补充 |

### 7.4 风险与边界

HR 场景需要注意：

- 不应基于敏感属性做判断。
- 不应替代最终招聘决策。
- 匹配度必须给出证据。
- 面试题生成应基于简历和 JD。
- 需要支持人工复核。

---

## 8. Agent 中台整体架构

### 8.1 逻辑架构

```text
用户 / 企业员工 / HR / 业务人员
        ↓
Agent 中台控制台
        ↓
Agent Runtime
        ↓
Intent / Router
        ↓
Skill Registry
        ↓
业务 Skill
  ├── 企业知识库问答 Skill
  ├── 企业报表分析 Skill
  ├── 合同/表单/发票抽取 Skill
  └── HR 招聘辅助 Skill
        ↓
RAG / VLM / LLM / Verifier / Trace
        ↓
结构化结果 + 证据页 + 运行详情
```

### 8.2 四个 Skill 的复用关系

| 底层能力 | 知识库问答 | 报表分析 | 合同/发票抽取 | HR 招聘辅助 |
|---|---|---|---|---|
| 文档解析 | ✅ | ✅ | ✅ | ✅ |
| 页面级建库 | ✅ | ✅ | ✅ | ✅ |
| RAG 检索 | ✅ | ✅ | ✅ | ✅ |
| VLM 页图理解 | ✅ | ✅ | ✅ | 可选 |
| Query Rewrite | ✅ | ✅ | ✅ | ✅ |
| Router | ✅ | ✅ | ✅ | ✅ |
| 结构化抽取 | 可选 | ✅ | ✅ | ✅ |
| Verifier | ✅ | ✅ | ✅ | ✅ |
| 证据页引用 | ✅ | ✅ | ✅ | ✅ |
| Trace / Eval | ✅ | ✅ | ✅ | ✅ |

### 8.3 实现状态

| 模块 | 状态 | 说明 |
|---|---|---|
| 企业知识库问答 | `implemented` | 当前项目核心能力，已封装为 `rag` Skill |
| 企业报表分析 | `partial` | 占比/差值/极值/求和等计算逻辑已完备，带可追溯 formula/inputs/confidence；生产前需扩充 BI 口径 gold set |
| 合同/表单/发票抽取 | `partial` | 字段级 schema(value/source/confidence/verified/masked)+ 字段校验 + 敏感字段脱敏已完备；生产前需接入真实 OCR 与字段级标注 |
| HR 招聘辅助 | `partial` | 技能匹配矩阵 + 匹配分 + 敏感属性合规拦截已完备；生产前需扩充简历/JD 语料与合规评测集 |
| Skill Registry | 已实现第一版 | `src/agent_center/skill_registry.py` 返回四个 Skill 元数据 |
| MCP Gateway | 接口骨架 | 可接企业系统和工具 |
| 生产压测 | 待执行 | 并发、延迟、容量需目标环境报告 |

> 说明：三个业务 Skill 的内部逻辑(计算/字段校验/脱敏/匹配矩阵/合规拦截)已完备并有测试覆盖，但因当前
> 仓库仅有 5 页 demo 数据、缺大规模真实语料与标注 gold set，状态如实保留为 `partial`，不声称生产级准召率。

### 8.4 代码实现说明

当前第一版中台后端采用增量方式实现，没有重写原有 RAG 主链路：

- `src/agent_center/schemas.py`：定义统一 `SkillSpec`、`SkillResult`、`AgentCenterRunRequest`。
- `src/agent_center/skill_registry.py`：注册四个 Skill，并提供统一查询入口。
- `src/agent_center/runtime.py`：根据 `workspace_id` 复用默认 QAEngine 或 workspace engine，负责统一执行。
- `src/agent_center/skills/rag_skill.py`：真实封装现有 QAEngine。
- `src/agent_center/skills/report_analysis_skill.py`：复用 `chart_qa`，从结构化 chart_data 与正文正则兜底抽取指标，支持占比/差值/极值/求和/查值计算，每个计算带 formula/inputs/confidence。
- `src/agent_center/skills/form_invoice_skill.py`：字段级抽取，每字段输出 value/source/confidence/verified/masked；带金额/日期/税号/单号格式校验与敏感字段脱敏。
- `src/agent_center/skills/hr_recruiting_skill.py`：按目标岗位生成技能权重匹配矩阵与匹配分；对涉及年龄/性别/民族/婚育等敏感属性的提问执行合规拦截(status=unsupported)。
- `data/agent_center/report_analysis_gold.json`、`data/agent_center/hr_compliance_samples.json`：计算 gold set 与合规评测样本。
- `src/api.py`：新增 `/agent-center/*` API，并保留原有 `/ask`、workspace、research、eval 入口不变。
- `web/agent_platform.html`：前端页面支持卡片选择、真实调用和 mock fallback(mock 结构与新 SkillResult 对齐)。

### 8.5 API 使用示例

列出所有 Skill：

```bash
curl -s http://127.0.0.1:8000/agent-center/skills | python -m json.tool
```

执行企业报表分析：

```bash
curl -s -X POST http://127.0.0.1:8000/agent-center/run \
  -H 'Content-Type: application/json' \
  -d '{
    "skill_name": "report_analysis",
    "query": "2024Q3 经营分析里哪个产品线销售额最高？",
    "workspace_id": null,
    "top_k": 3,
    "options": { "return_trace": true }
  }' | python -m json.tool
```

执行合同 / 表单 / 发票抽取：

```bash
curl -s -X POST http://127.0.0.1:8000/agent-center/run \
  -H 'Content-Type: application/json' \
  -d '{
    "skill_name": "form_invoice",
    "query": "采购单号是多少？",
    "top_k": 3,
    "options": { "return_trace": true }
  }' | python -m json.tool
```

### 8.6 当前实现状态

当前仓库已经完成第一版“可运行中台”闭环：

- `rag` Skill：真实实现，证据页、route/retrieval/verifier trace 可复用现有问答链路。
- `report_analysis` Skill：`partial`，占比/差值/极值/求和计算逻辑完备且有 gold set 覆盖，输出可追溯 formula。
- `form_invoice` Skill：`partial`，字段级 schema + 校验 + 脱敏完备，敏感字段以 masked 视图展示并提示人工确认。
- `hr_recruiting` Skill：`partial`，技能匹配矩阵 + 匹配分完备，对敏感属性提问合规拦截并有评测样本覆盖。
- 前端控制台：可通过 `/agent-platform` 调用真实 API，失败时自动回退 mock 结果。

> 依赖说明：`langgraph` 已固定为 0.2.x，与 `langchain-core` 0.2.x 配套(见 `requirements.txt`)。
> 此前 `langgraph` 未固定版本导致被装成 1.x，而 `langchain-core` 仍为 0.2.x，触发
> `Reviver.__init__() got an unexpected keyword argument 'allowed_objects'` 导入失败。降级后真实
> langgraph 编排链路恢复(不再走轻量 fallback 图)。

---

## 9. 数据与评测体系

### 9.1 评测对象

四个业务功能都需要评测，不应只靠演示样例。

| 功能 | 评测样本 |
|---|---|
| 企业知识库问答 | 文档问答 gold set |
| 企业报表分析 | 图表页数值问答、计算题 |
| 合同/表单/发票抽取 | 字段级标注样本 |
| HR 招聘辅助 | 简历-JD 匹配样本、人工审核结果 |

### 9.2 评测指标

| 指标 | 说明 |
|---|---|
| Recall@1/3/10 | 检索是否命中金标页 |
| MRR@10 | 正确页排序位置 |
| Relaxed Exact Match | 文本/数值答案宽松匹配 |
| Field Accuracy | 字段抽取准确率 |
| Router Accuracy | 路由分支是否正确 |
| Verifier Pass Rate | 校验通过率 |
| Fallback Rate | 回退触发率 |
| Latency | 阶段耗时、p50/p95/p99 |
| Evidence Coverage | 答案是否有证据页 |

### 9.3 金标数据建议

建议按功能拆分金标集：

```text
data/eval_sets/
  enterprise_qa/
  report_analysis/
  form_invoice_extraction/
  hr_recruiting/
```

每条样本建议包含：

```json
{
  "query": "这张发票的金额是多少？",
  "gold_answer": "12800.00",
  "gold_pages": ["doc_xxx_p3"],
  "category": "invoice_field_extract",
  "expected_skill": "form_invoice_extraction",
  "field_schema": {
    "amount": "number"
  }
}
```

---

## 10. 生产化设计

### 10.1 部署形态

开发期可以使用千问 API 或 OpenAI-compatible API 模拟模型能力；生产期应保持模型中立，支持：

- 企业私有模型。
- vLLM Serving。
- Milvus / Qdrant 向量库。
- Redis 缓存。
- Kafka 增量建库。
- Prometheus / Grafana / Sentry 监控。
- Docker / K8s 部署。

### 10.2 安全与权限

企业 Agent 中台需要：

- Workspace 隔离。
- 文档上传白名单。
- JWT / ACL / 用户组。
- 工具权限。
- 审计日志。
- 敏感信息脱敏。
- 高风险操作人工确认。
- Prompt Injection 防护。

### 10.3 可靠性

需要设计：

- 超时控制。
- 重试策略。
- Fallback 降级。
- 拒答机制。
- 错误码标准化。
- 幂等任务 ID。
- Trace 回放。
- 灰度发布。

---

## 11. 与普通 RAG Demo 的区别

| 维度 | 普通 RAG Demo | 本项目 |
|---|---|---|
| 文档类型 | 主要文本 | PDF/PPT/Excel/图表/扫描件/复杂版式 |
| 检索粒度 | 文本 chunk | 页面级 page_id + 证据页 |
| 多模态 | 弱 | 支持 VLM 页图理解 |
| 问题类型 | 单一问答 | 知识问答、报表分析、字段抽取、HR 匹配 |
| 路由 | 无 | Router 分支 |
| 校验 | 少 | Verifier + Fallback |
| 输出 | 自然语言 | 自然语言 + JSON + 表格 + 证据页 |
| 评测 | 少 | MRR / Recall / Relaxed EM / Router Accuracy |
| 工程化 | 本地 demo | Workspace、Trace、缓存、监控、生产接入骨架 |

---

## 12. 项目边界

当前文档不包含：

- 会议纪要 Agent：飞书、钉钉、腾讯会议已有成熟能力，本项目暂不作为重点。
- 机器人/具身智能控制：本阶段先聚焦企业 AI Agent 中台。
- 底层模型微调：当前主要复用 API / VLM / OpenAI-compatible / 可选自部署模型。
- 已验证百万页规模：仓库有生产接入点，但真实规模需要目标环境压测和评测报告。

合理对外口径：

> 本项目实现了企业多模态 RAG+Agent 的主体工程闭环，并将其升级抽象为企业 AI Agent 中台。当前以企业知识库问答为真实核心能力，向企业报表分析、合同/表单/发票抽取、HR 招聘辅助四个场景扩展。四个功能复用同一套文档解析、页面级建库、RAG 检索、VLM 页图理解、Router、Verifier、Trace 和评测体系。

---

## 13. 面试讲解口径

### 13.1 一分钟介绍

这个项目最初是企业内部多模态 RAG 问答系统，面向 PDF、PPT、Excel、合同、报表等图文混排文档。后来我把它升级为企业 AI Agent 中台：把现有 RAG 能力抽象成第一个真实的 RAG Skill，再向企业报表分析、合同/表单/发票抽取、HR 招聘辅助扩展。系统底层复用页面级建库、混合检索、VLM 页图理解、Router 分流、Verifier 校验、SSE Trace 和离线评测体系，避免每个业务场景重复造轮子。

### 13.2 技术亮点

- 多模态页面级 RAG，而不是单纯文本切块。
- `fact_qa` / `multi_page_qa` / `chart_qa` 三分支 Router。
- VLM 页图推理，适配图表、扫描件、PPT、复杂版式。
- Verifier + Fallback，降低无证据幻觉。
- Workspace 隔离和匿名持久会话。
- SSE Trace 和运行详情。
- 金标评测和批量报告。
- Milvus、ColPali、Redis、Kafka、监控等生产接入点。

### 13.3 项目边界回答

如果面试官问“这些功能都完全实现了吗”，可以回答：

> 当前仓库已经实现企业多模态 RAG+Agent 主体闭环，企业知识库问答是最完整的真实能力。企业报表分析已有 chart_qa 和 VLM 页图理解基础；合同/表单/发票抽取和 HR 招聘辅助可以复用现有文档理解、结构化抽取和证据校验链路，但还需要补业务 schema、权限、人工确认和评测集。生产规模和性能指标需要在目标部署环境重新压测和归档。

---

## 14. 高频 QA

### Q1：这个项目和普通知识库问答有什么区别？

普通知识库问答主要做文本检索。本项目面向企业真实的多模态文档，支持 PDF、PPT、Excel、图表、扫描件、复杂版式，并通过 Router、VLM、Verifier 和 Trace 组成完整 Agent 链路。

### Q2：为什么叫 Agent 中台？

因为它不是单一问答接口，而是把文档解析、检索、路由、生成、校验、结构化输出、证据页和评测沉淀成可复用底座。不同业务功能只是复用底座能力的不同 Skill。

### Q3：四个功能是不是四套系统？

不是。四个功能共用同一套多模态 RAG+Agent 底座。差异主要在 Prompt、Schema、Verifier 规则、输出格式和评测集。

### Q4：企业报表分析是不是要做 BI？

不是。它不是替代 BI，而是解决“文档型报表问答”：从 Excel、PPT、PDF 报表页中读取图表、数值和趋势，并给出证据页。

### Q5：合同/发票抽取是不是 OCR？

不只是 OCR。OCR 只识别文字，本项目还要理解字段语义、版式关系、证据页、缺失字段和风险提示，并输出结构化 JSON。

### Q6：HR 招聘辅助是不是会有合规风险？

有，所以它不能替代 HR 决策。它只做简历解析、JD 匹配、亮点风险总结和面试问题生成，结论必须有证据，敏感属性不作为判断依据。

### Q7：当前项目最成熟的是哪个功能？

企业知识库问答最成熟，因为它直接对应当前已经实现的多模态 RAG+Agent 主体能力。

### Q8：合同/发票和 HR 功能现在能不能实现？

技术上可以复用现有 VLM/RAG/结构化输出能力，但要产品化需要补字段 schema、业务规则、权限、人工确认和评测集。

### Q9：为什么不写会议纪要功能？

飞书、钉钉、腾讯会议已经有成熟会议纪要能力，这个功能对项目差异化帮助不大，所以本阶段不作为重点。

### Q10：这个项目下一步最该做什么？

优先把现有 RAG+Agent 抽象成标准 `RAG Skill`，再补 `Skill Registry` 的最小实现。随后选择企业报表分析或合同/发票抽取作为第二个可演示 Skill。

---

## 15. 下一步实施建议

### Phase 1：RAG Skill 标准化

- 把当前 RAG 问答链路封装为统一 Skill。
- 定义 input/output schema。
- 保留证据页、Trace、Verifier。

### Phase 2：企业报表分析 Skill

- 增强 `chart_qa`。
- 支持数值抽取和计算过程。
- 增加报表类评测样本。

### Phase 3：合同/表单/发票抽取 Skill

- 定义字段 schema。
- 输出 JSON。
- 增加字段级 Verifier。
- 增加人工确认机制。

### Phase 4：HR 招聘辅助 Skill

- 定义简历/JD schema。
- 输出匹配矩阵。
- 生成面试问题。
- 增加合规边界和人工复核。

### Phase 5：中台化能力沉淀

- Skill Registry。
- MCP Gateway。
- 权限与审计。
- 统一评测报告。
- 生产化部署与压测。
