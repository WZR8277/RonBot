# Agent 前沿知识库

本目录供「Agent 前沿信息助手」使用：存放**对 Agent 开发工程师有实际影响**的可靠事实与待验证信息，供 RAG 检索、订正与时间线补全。

## 用途

- **RAG 检索**：总结与订正阶段用 `rag_query` 检索已有事实与时间线，去重、区分「已知 vs 新进展」。
- **持久化**：可靠信息写入主库，存疑/简约源结论写入 `uncertain/`，便于后续人工审核或检索时降权。

## 何为「对工程师有价值」的前沿信息

| 维度 | 说明 | 示例 |
|------|------|------|
| 框架与协议 | 新框架、协议或大版本 | MCP、LangGraph、CrewAI、AutoGen |
| 基准与评估 | 新 benchmark、评估方法 | SWE-bench、AgentBench、WebArena |
| 模型与 API | 与 Agent 强相关的模型/API | 推理模型、function calling、长上下文 |
| 工程实践 | 部署、可观测、安全、成本 | 可观测性、提示注入防护、成本控制 |
| 论文与开源 | 重要论文、高星仓库 | ReAct、CoT、Tool Use、开源 Agent 项目 |
| 生态与招聘 | 技能需求、社区动态 | 岗位 JD 中的 Agent 技能、社区热点 |

知识库内容应围绕以上维度积累，便于检索时命中「对开发有影响」的要点。

## 目录结构

```text
knowledge_base/
  README.md     # 本说明
  facts.md      # 长期可靠事实、时间线、框架/术语列表
  uncertain/    # 待验证信息（注明来源与「待验证」）
    YYYY-MM-DD.md
  YYYY-MM-DD-agent-advances.md   # 可选：按日主库
```

## 约定

- **可靠信息**：写入 `facts.md` 或按日 `knowledge_base/YYYY-MM-DD-agent-advances.md`。格式需含：日期、来源、摘要、与工程师相关的要点。
- **存疑/待验证**：写入 `uncertain/YYYY-MM-DD.md`，注明「待验证」与来源。
- 只追加不删除；文件过大时可人工归档。
- 更新后由流水线或人工执行 **rag_index**，以更新 RAG 索引。

## 使用方式（路径别搞错）

- **本目录是仓库内示例**（`docs/knowledge_base/`），RAG 不会直接读这里。
- **运行时知识库**在 **workspace 下的 knowledge_base**：默认即 **`~/.nanobot/workspace/knowledge_base`**（若未改 workspace 配置）。
- 请将本目录的 **README.md**、**facts.md** 复制到上述路径，并在其下新建 **uncertain/** 目录。
- 前沿助手流水线会将订正后的可靠进展追加到主库、存疑到 uncertain，并调用 rag_index。
