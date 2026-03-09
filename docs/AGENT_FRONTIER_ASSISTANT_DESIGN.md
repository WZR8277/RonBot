# Agent 前沿信息助手 — 设计与实现指南

基于 nanobot 实现定时从网络获取 AI/Agent 前沿信息，结合本地知识库做总结，更新知识库并向 Telegram 推送。本文回答：如何实现、知识库如何准备、RAG 选型、以及是否使用 skills/subagents。

---

## 一、整体架构与数据流

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  定时触发 (Cron)                                                              │
│  例如: 每天 9:00 "执行 Agent 前沿周报任务"                                     │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  1. 信息采集                                                                  │
│  - web_search(Brave) / web_fetch 抓取：论文站、博客、GitHub、X、HN 等          │
│  - 可选：RSS/API 订阅（需自建 fetcher 工具或 skill 内脚本）                     │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  2. 与本地知识库结合 (RAG)                                                     │
│  - 从 knowledge_base 检索：与「agent/AI 进展」相关的既有事实                  │
│  - 用于去重、补全时间线、区分「已知 vs 新进展」                                │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  3. 总结与分级                                                                │
│  - LLM 总结：Agent 相关进展 + 对 Agent 开发工程师的展望/影响                   │
│  - 对每条信息打可靠性/重要性：可靠 → 主知识库；存疑 → 次要/待验证              │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  4. 更新本地知识库                                                            │
│  - 高可靠：写入 knowledge_base/ 主文档或向量库                                 │
│  - 低可靠：写入 knowledge_base/uncertain/ 或带 metadata 的「待验证」区         │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  5. 推送 Telegram                                                             │
│  - 通过 message 工具或 bus.publish_outbound(OutboundMessage(telegram, ...))   │
│  - 内容：总结摘要 + 可选「详情见知识库」链接                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

实现上可以有两种方式：

- **方式 A（推荐起步）**：单次 Cron 任务，由主 Agent 在一次会话中顺序执行：采集 → RAG 检索 → 总结 → 写回知识库 → 发 Telegram。逻辑简单，易调试。
- **方式 B**：Cron 只发一条「开始前沿周报」消息，主 Agent 用 **spawn** 工具起一个 **Subagent** 在后台执行上述流水线，完成后 Subagent 通过 `_announce_result` 把总结发回 Telegram。适合希望不阻塞网关、且总结耗时较长的场景。

---

## 二、具体实现要点

### 2.1 定时任务（Cron）

- 使用 nanobot 已有 **CronService** 与 **CronTool**。
- 在 Telegram（或 CLI）里对 agent 说：「每天早 9 点执行 Agent 前沿周报，总结发到当前对话」，agent 会调用 `cron` 的 `add`，例如：
  - `cron_expr`: `0 9 * * *`，`tz`: `Asia/Shanghai`
  - `message`: 写死为一段「任务说明」，例如：
    - 「请执行 Agent 前沿信息助手流程：1) 用 web_search 搜索近期 AI/Agent 进展；2) 读取 workspace 下 knowledge_base 中相关文档；3) 总结进展与对开发者的展望；4) 将可靠信息追加到 knowledge_base，存疑的写入 uncertain；5) 用 message 工具把总结发到当前 Telegram 对话。」
  - `deliver: true`，`channel: telegram`，`to: <chat_id>`
- Cron 触发时，`on_cron_job` 会以 `agent.process_direct(reminder_note, session_key=f"cron:{job.id}", ...)` 执行，agent 看到的「用户消息」就是上面的任务说明，从而按步骤调用 web、读文件、写文件、message。

**无需改 nanobot 核心**，只需：
- 在 workspace 下准备好知识库目录与初始文档（见下节）；
- 可选：在 workspace 放一个 **AGENTS.md**（或 HEARTBEAT.md）明确「前沿周报」的步骤，方便 agent 一致执行。

### 2.2 信息采集来源

- **已有能力**：`web_search`（Brave）、`web_fetch`（URL 正文抽取）。在任务说明里明确让 agent 用这些工具搜索例如：
  - "AI agent framework 2025"
  - "LLM agent benchmark latest"
  - "autonomous agent research arxiv"
  - 以及你关心的博客、GitHub 仓库、X 账号等（通过 search 拿到 URL 再用 web_fetch）。
- **扩展**：若需要 RSS/API，可以：
  - 在 **nanobot 里加一个 Tool**（如 `rss_fetch`），在 Cron 任务说明中让 agent 先调该工具再总结；或
  - 用外部 cron 脚本拉 RSS 到 workspace 下某目录（如 `workspace/inbox/rss/*.md`），任务说明里写「先读 inbox/rss 下今日文件，再 web_search 补充」。

### 2.3 总结后更新知识库

- **可靠信息**：让 agent 用 **write_file / edit_file** 写入 `workspace/knowledge_base/` 下主文档（如按周/月的 `YYYY-MM-DD-agent-advances.md`），或追加到 `knowledge_base/facts.md`。
- **存疑信息**：写入 `workspace/knowledge_base/uncertain/` 或同一文件中的「待验证」段落，并在任务说明里约定格式（如 `## 待验证\n- 来源: ... 内容: ...`），便于日后人工审核或二次 RAG 时降权。

### 2.4 向 Telegram 发送总结

- 若 Cron 任务里 `deliver: true` 且 `channel`/`to` 正确，`on_cron_job` 会在 agent 返回后把 `response` 用 `bus.publish_outbound(OutboundMessage(..., content=response))` 发到该 Telegram 会话。
- 若希望 agent 在**执行过程中**主动发一条（例如先发「正在采集…」再发最终总结），可在任务说明里要求 agent 使用 **message** 工具，并确保 message 的 `channel`/`chat_id` 与 cron 的 `channel`/`to` 一致（cron 触发时会把当前 session 的 channel/chat_id 设好，message 工具会继承）。

---

## 三、初始知识库如何准备

目标：给 RAG 和总结提供「基础知识」，避免每次从零开始，并让 agent 能区分「已知」与「新进展」。

### 3.1 目录结构建议（在 workspace 下）

```text
workspace/
  knowledge_base/
    README.md              # 说明本目录用途与更新规范
    facts.md               # 长期积累的可靠事实（时间线、术语、框架列表等）
    uncertain/             # 待验证或低置信度信息
      YYYY-MM-DD.md
    ...                    # 可选：按主题/日期的文件
  memory/                  # 现有 nanobot memory（MEMORY.md, HISTORY.md）
  AGENTS.md                # 可选：写清「前沿周报」的步骤与规范
```

示例初始文件见仓库内 `docs/knowledge_base/`（README.md、facts.md），可复制到 workspace 下使用。

### 3.2 建议的初始内容

- **facts.md**（可由你或 agent 先写一版）：
  - 一段「本知识库用于 Agent/AI 前沿信息，供定时总结与 RAG 检索」的说明；
  - 已知的 Agent 框架/术语/重要论文或产品名称（如 LangChain、AutoGPT、CrewAI、ReAct、Tool Use 等），便于检索与去重；
  - 可选：简短时间线（如「截至 YYYY-MM，主流方向是 …」），便于后续总结写「与上周/上月对比」。
- **README.md**：约定哪些内容写进 facts.md、哪些进 uncertain/，以及文件名/格式（如日期、来源必填）。
- 若使用向量库（见下节），初始可对 `facts.md` 做一次索引，之后每次「更新知识库」时再增量索引新段落。

这样「开始的知识库」就具备：**用途说明 + 基础事实 + 更新规范**，agent 或 RAG 检索都能用上。

---

## 四、RAG 选型建议（前沿且可落地）

在「定时总结 + 更新知识库」的场景下，检索主要用于：**用已有知识库辅助总结、去重、补全时间线**，数据量一般是万级段落以内，无需一开始就上大规模分布式。

### 4.1 推荐方向（2024–2025 常见做法）

- **Hybrid Search（向量 + 关键词）**  
  向量负责语义相似，BM25/关键词负责精确匹配（如框架名、论文 ID）。两者用 **RRF（Reciprocal Rank Fusion）** 合并，通常比单向量检索提升明显。
- **轻量向量库**  
  数据量 &lt; 约 5M 向量时，**pgvector（PostgreSQL）** 或 **Chroma** 即可；若希望与 nanobot 同栈、少依赖，可用 **Chroma** 或 **SQLite + sqlite-vec** 做原型。
- **Rerank（可选）**  
  若检索段落较多，可用小型 cross-encoder 或 API 做 rerank，提高「与当前总结问题」最相关的几条再喂给 LLM，减少噪音与 token。
- **分块与元数据**  
  按段落或按节 chunk，每条带 `source`、`date`、`reliability`（来自 facts / uncertain）等 metadata，便于过滤「只检索高可靠」或「也包含待验证」。

### 4.2 在 nanobot 中的落地方式

- **方案 1（最小实现）**  
  不做向量库，知识库就是 markdown 文件。任务说明里要求 agent：先用 **read_file** / **list_dir** 读 `knowledge_base/`，再结合 web 结果做总结与写回。适合知识库不大（几十个文件以内）、追求零依赖时。
- **方案 2（文件 + 简单检索）**  
  仍以文件为主，在 workspace 下放一个脚本或 Tool：根据关键词/标题做 **grep 或简单 BM25**（如 rank_bm25） over 知识库文本，返回匹配片段路径与摘要，agent 再按路径 read_file。这样已有「关键词 + 简单排序」，无需服务化向量库。
- **方案 3（完整 RAG 服务）**  
  单独起一个 RAG 服务（或 nanobot 内 Tool 调本地脚本）：  
  - 用 Chroma/pgvector 存 embedding，可选 BM25 做 hybrid；  
  - 提供「query → top-k 段落 + metadata」接口；  
  - agent 在 Cron 任务里先调该 Tool 取「与本周报相关的知识库内容」，再与 web 结果一起总结。  
  实现时可复用 nanobot 的 LLM provider 做 embedding（若支持）或使用小模型/OpenAI embedding API。

### 4.3 本项目已实现的 RAG（创新点）

本项目在 `nanobot/rag/` 与 `nanobot/agent/tools/rag.py` 中实现了**可选的 RAG 模块**，体现以下创新：

1. **Hybrid 检索（向量 + BM25，RRF 融合）**  
   - 向量检索：Chroma 持久化存储，支持进程重启后从磁盘恢复；Embedding 支持 **OpenAI API** 或 **sentence-transformers 本地模型**（安装 `nanobot-ai[rag]` 即可）。  
   - 关键词检索：**BM25**（rank_bm25）对同一批 chunk 建索引，与向量结果用 **RRF（Reciprocal Rank Fusion, k=60）** 合并，无需分数归一化即可融合两种排序。

2. **可靠性分级检索（Reliability-Aware Retrieval）**  
   - 知识库路径约定：`knowledge_base/uncertain/` 下为「待验证」，其余为「可靠」。分块时自动打上 `reliability` 元数据。  
   - 检索参数 `reliability`：`reliable_only`（仅主库）、`prefer_reliable`（可靠结果 RRF 加权 1.5x）、`all`（不区分），便于总结时**优先依据可靠事实、可选纳入待验证**。

3. **Markdown 感知分块与元数据**  
   - 按 `##`/`###` 切分，保留章节上下文；长块按句/段二次切分并带重叠。  
   - 每条 chunk 带 `source`（相对路径）、`date`（从文件名或正文抽取）、`reliability`，写入 Chroma 的 document + metadata，便于 BM25 与重启后恢复。

4. **Agent 侧工具**  
   - **rag_index**：从 `workspace/knowledge_base` 构建/重建索引（首次或更新后调用）。  
   - **rag_query**：自然语言 query + `top_k` + `reliability`，返回带来源与可靠性的片段，供总结流程使用。  
   - 配置：`config.tools.rag`（enabled、knowledge_base_subdir、embedding_model、api_key）；未配置或未安装 `[rag]` 时可不注册工具，agent 退化为读文件。

**安装与配置**：`pip install nanobot-ai[rag]`；在 `~/.nanobot/config.json` 的 `tools.rag` 中可选设置 `embeddingModel`、`apiKey`（不设则使用 sentence-transformers 本地）。前沿周报流程中优先调用 `rag_index` + `rag_query` 再总结，即可体现 RAG 创新。

---

## 五、Skills 与 Subagents 的使用建议

### 5.1 是否用 nanobot 的 Skills（SKILL.md）

- **适合用**：  
  - 把「Agent 前沿周报」的**固定流程、输出格式、知识库更新规范**写进一个 **nanobot skill**（例如 `nanobot/skills/agent-frontier/` 下 SKILL.md）。  
  - 这样 Cron 触发的任务说明可以很短（如「按 agent-frontier skill 执行本周周报」），具体步骤、来源建议、可靠/存疑的区分标准都在 skill 里，便于维护和复用。
- **实现方式**：  
  - 在 `nanobot/skills/` 下新建 `agent-frontier/SKILL.md`，在 description 里写明「用于定时执行 AI/Agent 前沿信息采集、结合知识库总结、更新知识库并推送到 Telegram」。  
  - 正文写清：用什么工具、先搜什么关键词、读哪些目录、总结应包含哪些章节、可靠 vs 存疑的写法、最后如何调用 message。  
  - 这样主 agent 在 Cron 里收到「执行前沿周报」时，会加载该 skill 并按步骤执行。

### 5.2 是否用 Subagents（spawn）

- **适合用**：  
  - 当单次「采集 + RAG + 总结 + 写回 + 发 Telegram」耗时较长（例如 2–5 分钟），你希望网关不阻塞、或希望用户立刻得到「已开始，完成后会发到 Telegram」的反馈时，可以让主 agent 收到 Cron 后**只做一件事**：调用 **spawn**，任务描述为上述完整流程；Subagent 在后台跑完后再通过 `_announce_result` 把总结发到 origin channel（Telegram）。
- **注意**：  
  - Subagent 当前没有 message 工具，但**结果会通过 bus 发回** `origin_channel`/`origin_chat_id`，所以 Telegram 仍能收到最终总结。  
  - 若你希望「进行中」也发一条到 Telegram，需要在主 agent 里先自己用 message 发「周报生成中…」，再 spawn；Subagent 完成时再发最终总结。

结论：**Skills 强烈建议用**（把流程与规范固化）；**Subagents 在需要「长时间任务、不阻塞」时用**，否则单次 Cron 内顺序执行即可。

---

## 六、Cursor 侧的 Skills / Rules（可选）

- **Cursor Skills（.cursor/skills/ 或 ~/.cursor/skills/）**：教的是「Cursor IDE 里的 AI 如何写代码、做 code review」等，和 nanobot 运行时是两套系统。若你希望 Cursor 在**编辑 nanobot 或该助手相关代码**时更一致（例如改 skill、改 Cron 任务说明），可以加一个 Cursor skill，描述为「在 nanobot 中维护 agent-frontier 流程与知识库结构时，遵循本文档的目录与约定」。
- **Cursor Rules（.cursor/rules/）**：可以加一条规则，例如对 `**/agent-frontier/**` 或 `**/knowledge_base/**` 的修改要符合当前设计文档中的目录与命名约定。

二者都是**开发时**的约束与提示，不参与 nanobot 的定时任务执行；nanobot 的「技能」是 `nanobot/skills/` 下的 SKILL.md。

---

## 七、实施步骤小结

1. **Workspace 与知识库**  
   - 在 workspace 下建 `knowledge_base/`、`knowledge_base/uncertain/`，写好 `README.md` 和初始 `facts.md`。

2. **Nanobot Skill**  
   - 新增 `nanobot/skills/agent-frontier/SKILL.md`，写清：采集来源、检索方式（先读 knowledge_base 或调 RAG）、总结结构、可靠/存疑的落库规则、发 Telegram 的方式。

3. **Cron 任务**  
   - 在 Telegram 对 agent 说：每天 9 点执行「按 agent-frontier 做 Agent 前沿周报并发到当前对话」，让 agent 用 cron add 创建任务（deliver=true，channel=telegram，to=当前 chat_id）。

4. **RAG（已实现）**  
   - 安装 `pip install nanobot-ai[rag]`，在流程中先 `rag_index` 再 `rag_query` 获取知识库上下文；详见上文 4.3 与 `nanobot/skills/agent-frontier/SKILL.md`。

5. **可选：Subagent**  
   - 若希望异步，把 Cron 的 message 改为「spawn 一个子任务：按 agent-frontier 执行周报并通知我」；主 agent 发一条「已开始」后 spawn，Subagent 完成后自动发总结到 Telegram。

6. **可选：Cursor**  
   - 为项目加 .cursor 的 skill/rule，约束知识库与 agent-frontier 的编辑规范。

按以上顺序实现，即可在现有 nanobot 上跑通「定时采集 → 知识库 + RAG → 总结 → 更新知识库 → Telegram 推送」，并保留用 skills 与 subagents 扩展的清晰路径。
