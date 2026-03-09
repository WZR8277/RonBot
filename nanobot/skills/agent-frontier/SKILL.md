---
name: agent-frontier
description: Run the Agent/AI frontier digest pipeline: fetch from web, combine with local knowledge_base, summarize for agent engineers, update knowledge base (reliable vs uncertain), and send summary to the current channel (e.g. Telegram).
---

# Agent 前沿信息助手

按固定流程执行：从网络采集 AI/Agent 前沿信息，结合 workspace 下 `knowledge_base`，总结进展与对开发者的影响展望，更新知识库（可靠信息主库、存疑进 uncertain），并向当前会话发送总结。

## 何时使用

- 用户要求「执行 Agent 前沿周报」「跑一次前沿总结」「按 agent-frontier 做周报」时；
- Cron 定时任务的消息内容要求执行本流程时。

## 流程（按顺序执行）

1. **采集**
   - 使用 `web_search` 搜索近期 AI/Agent 相关关键词（如 "AI agent framework 2025", "LLM agent benchmark", "autonomous agent research" 等），用 `web_fetch` 抓取需要详读的 URL 正文。
   - 若有 `workspace/inbox/` 或 `knowledge_base/inbox/` 下的今日/本周文件（如 RSS 拉取结果），先 `list_dir` / `read_file` 读取。

2. **结合知识库（RAG）**
   - **优先使用 RAG 工具**（若已安装 `pip install nanobot-ai[rag]`）：先调用 `rag_index` 确保知识库已建索引，再调用 `rag_query` 以「近期 Agent/AI 进展」「agent 框架与基准」等 query 检索，用于去重与补全时间线。RAG 采用 **Hybrid 检索（向量 + BM25，RRF 融合）** 与 **可靠性分级**（`reliability: prefer_reliable` 优先主库，可排除 uncertain）。
   - 若无 RAG 工具，则读取 `knowledge_base/README.md` 与 `knowledge_base/facts.md`（若存在）。

3. **总结**
   - 用 LLM 总结：近期 Agent/AI 进展 + 对 **Agent 开发工程师** 的**影响与展望**（技术选型、技能、趋势）。
   - 对每条信息标注可靠性：**可靠**（官方/论文/可信来源）→ 准备写入主库；**存疑**（传闻、单源）→ 写入 uncertain。

4. **更新知识库**
   - **可靠**：用 `write_file` 或 `edit_file` 追加到 `knowledge_base/facts.md`，或新建 `knowledge_base/YYYY-MM-DD-agent-advances.md`，格式含日期、来源、摘要。
   - **存疑**：写入 `knowledge_base/uncertain/YYYY-MM-DD.md`，格式注明「待验证」及来源。

5. **发送总结**
   - 使用 `message` 工具，将面向用户的**总结摘要**（含要点与对开发者的展望）发到当前 channel/chat_id；可注明「详情已写入 knowledge_base」。

## 输出格式建议

总结发给人看的部分建议包含：
- **本周/本期要点**（3–5 条）
- **对 Agent 开发工程师的展望**（技术栈、工具、能力方向）
- **可靠 vs 待验证**（可简要列出，详情在知识库）

## 知识库约定

- 主事实：`knowledge_base/facts.md` 或按日的 `knowledge_base/YYYY-MM-DD-*.md`。
- 待验证：`knowledge_base/uncertain/YYYY-MM-DD.md`。
- 不删除既有内容，只追加；若文件过大可建议人工归档。

## RAG 工具说明（可选依赖 [rag]）

- **rag_index**：从 `knowledge_base/` 构建/重建索引（Markdown 按节分块，带 source/date/reliability 元数据）。
- **rag_query**：`query`（自然语言）、`top_k`（条数）、`reliability`（`reliable_only` | `prefer_reliable` | `all`）。检索结果会标注来源与可靠性，便于总结时区分已知与待验证。
