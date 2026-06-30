# docs 文档目录

本目录用于存放可公开提交的项目说明、架构设计、测试验证和运维资料。
当前项目主线已经收敛为：**企业级 AI Agent 中台 + 四大企业功能 + 多模态 RAG+Agent 底座**。

## 推荐阅读顺序

| 顺序 | 文档 | 用途 |
|---|---|---|
| 1 | [产品功能总览.md](产品功能总览.md) | 项目总入口：当前能力、启动方式、配置开关、文档索引、里程碑、`/agent-center/*` API |
| 2 | [企业级AI Agent中台项目.md](企业级AI%20Agent中台项目.md) | 新版正式项目文档：四大企业功能、统一技术底座、代码实现说明、API 示例 |
| 3 | [企业多模态研究Agent架构与API.md](企业多模态研究Agent架构与API.md) | API、Workspace、ResearchJob、运行边界 |
| 4 | [testing-validation-guide.md](testing-validation-guide.md) | 测试、评测、压测和提交前验证 |
| 5 | [kafka-reindex.md](kafka-reindex.md) | Kafka 增量建库、幂等、DLQ 设计 |

## 当前主线文档

- [产品功能总览.md](产品功能总览.md)
- [企业级AI Agent中台项目.md](企业级AI%20Agent中台项目.md)
- [企业多模态研究Agent架构与API.md](企业多模态研究Agent架构与API.md)
- [testing-validation-guide.md](testing-validation-guide.md)
- [kafka-reindex.md](kafka-reindex.md)

补充说明：

- 页面入口：`/chat` 为原有问答页，`/agent-platform` 为新的 Agent 中台页。
- Skill API：`GET /agent-center/skills`、`GET /agent-center/skills/{skill_name}`、`POST /agent-center/run`。
- Skill 状态：`rag` 为 `implemented`；`report_analysis`/`form_invoice`/`hr_recruiting` 内部逻辑(计算/字段校验脱敏/匹配矩阵合规)已完备并有测试覆盖，因缺大规模真实语料如实保留为 `partial`。

## 架构图与专项资料

- [robot_agent_runtime_architecture.md](robot_agent_runtime_architecture.md)：Robot Agent Runtime 架构图与 SVG。当前阶段作为后续专项资料保留，不属于企业中台主线。
- `assets/`：存放 SVG、可缩放查看器等图形资产。

## 归档候选

以下文档更像历史提示词或阶段性对话记录，建议后续统一移动到 `docs/archive/`：

- `企业多模态研究Agent增量升级提示词.md`
- `项目改造对话记录_20260625.md`
- 旧的机器人专项图文档（如果当前阶段不再使用）

移动前需要确认 README 或其他文档是否引用它们，避免链接失效。

## private 目录说明

`private/` 中是本地私有参考资料、导师文档、简历和审计材料，默认不作为公开文档提交：

- `private/多模态知识库问答RAG+Agent项目.md`
- `private/多模态知识库问答RAG+Agent项目_QA版.md`
- `private/导师文档实现度审计.md`
