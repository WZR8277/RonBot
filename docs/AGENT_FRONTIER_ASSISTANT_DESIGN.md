# Agent 前沿信息助手 — 设计与实现指南（2026）

面向 Agent 开发工程师的定时前沿信息流水线：**采集（工具，无 LLM）→ 总结（多源必做）→ 订正（含去重）→ 写回知识库、rag_index（先存有用知识）→ 影响分析（最后给用户看）→ 推送**。流程由 **Skills** 管理；**触发由 Cron 自动执行，无需用户发消息**；用户发消息仅为可选手动再跑一次。

---

## 一、目标与「前沿信息」定义

### 1.1 目标

- 定时（如每日/每周）从多种数据源采集 AI/Agent 相关动态。
- 对** Agent 开发工程师**产出：**对实际工作有影响**的总结与指导（技术选型、协议与框架、基准与论文、工程实践、技能趋势）。
- 结合本地知识库做去重、订正与时间线补全，并写回知识库供后续 RAG 检索。

### 1.2 何为「前沿且对实际工作有影响」

对 agent 开发工程师有价值的信息包括但不限于：

| 类型 | 说明 | 示例 |
|------|------|------|
| **框架与协议** | 新框架、协议或大版本更新 | MCP、LangGraph、CrewAI、AutoGen、多模态 Agent SDK |
| **基准与评估** | 新 benchmark、排行榜、评估方法 | SWE-bench、AgentBench、WebArena、Agent 能力维度 |
| **模型与 API** | 与 Agent 强相关的模型/API 更新 | 推理模型、function calling、长上下文、多模态 |
| **工程实践** | 部署、可观测、安全、成本 | Agent 可观测性、提示注入防护、成本控制 |
| **论文与开源** | 重要论文、高星仓库、最佳实践 | ReAct、CoT、Tool Use、开源 Agent 项目 |
| **生态与招聘** | 技能需求、岗位描述、社区动态 | 岗位 JD 中的 Agent 技能、社区讨论热点 |

知识库应围绕上述维度积累「可靠事实」与「时间线」，便于 RAG 检索时区分已知与新增、并支撑「对开发者的影响与展望」分析。

---

## 二、数据源（2026）与采集策略

### 2.1 数据源分类

- **高可靠、优先**：论文（arXiv）、官方博客、GitHub 仓库/Release、HN、部分技术媒体。用于主知识库与结论。
- **补充、需交叉验证**：X/Twitter、Reddit、技术论坛。可用于趋势与舆情，写入时标为「待验证」或仅作参考。
- **简约、可作为数据源**：小红书、即刻等短文/帖子，信息密度低但反应热点与落地场景；可作为**补充数据源**，与高可靠源结合使用，结论需与主源对照。

### 2.2 具体来源与工具用法

- **默认 queries 使用 site: 限定**：为保证 Brave Search 能稳定抓取，`frontier_ingest` 不传 `queries` 时使用 **site:** 限定来源（如 `site:openai.com/blog agent`、`site:anthropic.com agent`、`site:huggingface.co/blog agent`）。可抓取清单见 2.2.1。
- **中文社区**：需传 `queries` 如 `site:36kr.com AI agent`、`site:qbitai.com agent`，并设 `search_lang=zh`。

| 来源 | 获取方式 | 说明 |
|------|----------|------|
| **Brave Search** | `web_search` / frontier_ingest | 通用检索；默认用 site: 限定到官方博客等易抓取站。 |
| **arXiv** | `web_search("arxiv agent LLM")` + `web_fetch` | 论文摘要与链接。 |
| **GitHub** | `web_search("site:github.com agent framework")`，必要时 `web_fetch` | 仓库、Release、Star 趋势。 |
| **HN / 技术博客** | `web_search` + `web_fetch` | 深度文章，优先抓正文。 |
| **X / 小红书 / 即刻** | `web_search` 或 RSS/API（若已接入） | 短文与讨论，用于热点与场景补充。 |

#### 2.2.1 优先可抓取来源（适合 Brave + site:）

优先选**官方博客、权威科技媒体、开源社区**，这些被搜索引擎广泛收录、易抓取。不传 `queries` 时默认用其中一部分；需要更多可传 `queries` 如：

- 一手权威：`site:openai.com/blog agent`、`site:anthropic.com agent`、`site:blog.google/technology agent`、`site:huggingface.co/blog agent`、`site:deepmind.google agent`
- 中文：`site:36kr.com AI agent`、`site:qbitai.com agent`、`site:jiqizhixin.com agent`、`site:csdn.net AI agent`
- 开源：`site:github.com agent framework`

### 2.3 关键词与「前沿」：不重复、用时间范围

- **不在关键词里塞年份**：「前沿」已经表示要近期内容，再在每条 query 里加 2026/2027 既重复又容易过期。默认查询只写**主题**，例如：`AI agent framework`, `LLM agent benchmark`, `MCP model context protocol`, `agentic AI engineering`, `autonomous agent research arxiv`。
- **前沿由 freshness 负责**：默认 `freshness=pw`（近 7 天），用**时间范围**保证结果够新；需要更近可传 `pd`（24 小时），放宽可传 `pm`（31 天）或 `py`（1 年），不需要时间过滤时显式传 `freshness=""`。

### 2.4 关键词别太多：更短、更少反而容易有结果

- 单条 query **词太多或太长**，容易把结果限得太死、反而少。默认用 **2–4 个词**的短 query（如 "AI agent framework"、"agent research arxiv"），默认只跑 **3 条 query**，需要再自己加。
- 若发现某次结果很少，可尝试：少写几个词、减少 query 数量，或把 `freshness` 放宽（如改成 `py` 或 `""`）。

### 2.5 每条数据源查多少条合适

- **推荐 3–5 条/query**：每条 query 抓 3–5 个 URL，兼顾多样性和可控量；太多则噪音大、后续总结 token 易爆，太少则容易漏。
- 默认 **count_per_query=3**；若要更全可调到 4–5，不推荐长期用 8–10（除非只跑很少几条 query）。

**可调参数**（`frontier_ingest`）：

| 参数 | 含义 | 说明 |
|------|------|------|
| `queries` | 自定义搜索词（仅主题） | 不传则用默认；前沿靠 freshness，不靠关键词加年份 |
| `count_per_query` | 每条 query 抓取的 URL 数 | 推荐 3–5，默认 3 |
| `max_queries` | 默认 query 只跑前 N 条 | 1–20，不传则跑全部 |
| `freshness` | 结果时间范围 | 默认 **pw**（7 天）；`pd`=24h，`pm`=31 天，`py`=1 年；传 `""`=不按时间过滤 |

**按时间最新排序**：Brave Search API **没有**「按发布时间排序」的参数，只有 **freshness** 用来**过滤**时间范围（pd/pw/pm/py）。在给定时间范围内的结果仍是**相关性排序**，无法实现「严格按时间从新到旧」。若想更偏新，只能把 `freshness` 收窄（如用 `pw` 或 `pd`），这样结果池本身就更近期。

采集时按「高可靠源优先、简约源补充」的顺序组织，并在每条材料上标注**来源类型**（可靠/待验证/简约），供后续总结与落库使用。

### 2.6 抓取相关工具的返回类型一致吗？需要那么多工具吗？

- **返回类型不一致**，靠**调用的工具名**区分：
  - **web_search**：返回**纯文本**，多行「标题 + URL + snippet」，无正文。
  - **web_fetch**：返回**JSON 字符串**，包含 `url`、`text`（正文）、`status`、`truncated` 等，适合单 URL 抓正文。
  - **frontier_ingest**：**不返回正文内容**，只把结果写入 `inbox/frontier-YYYY-MM-DD-raw.md`，工具返回一行说明（写入了多少 sections、总字符数等）。
- **是否需要多个工具**：**前沿流水线里用 frontier_ingest 一条龙即可**（内部会调搜索 + 对每条 URL 抓正文并写文件）。**web_search** 和 **web_fetch** 留给主 agent **临时单次查询**或**补查某一条 URL** 时用（例如 skill 里要「再查一个关键词」或「抓某个链接正文」时），不必在流水线里重复造轮子。

---

## 三、长内容与多源合并策略

### 3.1 Agent 如何知道「内容过长」

- **采集结果文件**：`frontier_ingest` 写入的 raw 文件中，每个 Source 的标题带有 **`[N chars]`**（如 `### Source 1: 标题 (url) [3500 chars]`），agent 用 `read_file` 读到后即可看到每段长度。
- **read_file 返回值**：整份内容在工具返回里，模型能根据字符量判断是否超长。
- **约定阈值**：在 Skill 中约定「单段超过约 2000 字则先调用 **summarize** 再合并」。agent 看到 `[3500 chars]` 或整段很长时，应对该段调用 `summarize(text=...)`，用返回的摘要参与合并。

### 3.2 为何一定是多数据源、怎么处理

- 采集阶段**始终是多数据源**：`frontier_ingest` 按多条搜索 query 执行，每条 query 下有多条 URL，输出文件里**按 Query / Source 分块**，天然多源。无需「判断是否多源」——**每次都按「分源摘要 → 再合并」**处理。
- **分源摘要**：每个 `### Source ...` 块先单独总结成 3–5 条要点（若该块超过约 2000 字则先对块内容调 `summarize`）。
- **合并摘要**：把各源摘要拼成「本期采集摘要」，总长控制在约 4000 字内，再进入订正阶段。

### 3.3 如何保证是「前沿」、与上次重复怎么办

- **前沿**：由 **freshness** 默认近 7 天（pw）限制结果时间范围；Brave 在该范围内按相关性排序。
- **与上次重复**：订正步骤通过 **rag_query_dedup** 查知识库做**知识去重**：只要某条进展的**内容**在知识库中已有（或表述不同但语义等价），即视为重复，不是文字完全一致才算重复；只标新进展与变化。

### 3.4 流程中的位置

```
frontier_ingest(无 LLM) → raw 文件(每段标 [N chars])
                ↓
Agent: read_file → 按源摘要(超长则 summarize) → 合并 → 本期采集摘要
                ↓
rag_query_dedup(知识库，query 由本期摘要构造)
                ↓
订正(去重、标新进展) → 订正后摘要
                ↓
写回知识库 + rag_index（先存有用知识）
                ↓
影响分析（对 Agent 开发工程师的指导，最后给用户看）+ 推送
```

---

## 四、知识库设计与 RAG

### 4.1 知识库的用途

- **RAG 检索**：为「总结与订正」提供已有事实与时间线，用于去重、补全、区分「已知 vs 新进展」。
- **持久化**：可靠信息写入主库，存疑/简约源结论写入 `uncertain/`，便于后续人工审核或降权检索。

### 4.2 为何使用 Markdown 文件

- 当前 RAG 实现（`nanobot/rag/`）**仅索引 knowledge_base 下的 facts.md 与 uncertain/*.md**（README.md 等不参与检索）；对它们分块、打 metadata（source、date、**reliability**），并写入向量库 + BM25 索引。**建索引时按路径分别打可信度标签**：非 uncertain 路径 → reliable，uncertain/ 下 → uncertain，供 rag_query 按可靠性过滤。
- 知识库「准备 md 文件」的含义是：
  1. **初始**：由人或在流程外准备好 `README.md`、`facts.md`（见下节），描述用途与基础事实。
  2. **运行中**：流水线把订正后带【可靠】标签的**追加写入** `knowledge_base/facts.md`（**必须 append=true**）；带【待验证】的写入 **knowledge_base/uncertain/YYYY-MM-DD.md**。
  3. 写入后调用 **rag_index** 重建索引（仅索引 facts.md 与 uncertain/*.md，不含 README；按路径打可信度），后续 **rag_query** 即可按 reliable/uncertain 检索。

因此「准备 md」不是让工程师手写所有前沿内容，而是：**约定目录与格式，由助手把采集→订正后的结果写入 md，再由 RAG 索引**。

### 4.3 知识库路径（别搞错）

- **运行时知识库路径**：**`<workspace>/knowledge_base`**。其中 `<workspace>` 由配置决定，默认为 **`~/.nanobot/workspace`**，即默认完整路径为 **`~/.nanobot/workspace/knowledge_base`**。
- **仓库内示例**：**`docs/knowledge_base/`**（README.md、facts.md）只是**模板**，RAG 不会读这里。需要把该目录下的内容**复制到**上面的运行时路径下使用。
- **nanobot init 不会自动创建 knowledge_base**：`sync_workspace_templates` 只创建 memory、skills 等，不会建 `knowledge_base/`。你要自己建目录并复制 `docs/knowledge_base/README.md`、`docs/knowledge_base/facts.md` 到 `~/.nanobot/workspace/knowledge_base/`（若用默认 workspace），并建 `uncertain/` 子目录。

**workspace 里已有的 md 和 skills 是干嘛的？**  
`~/.nanobot/workspace` 下 nanobot 自带的文件/目录是**通用工作区模板**，与「前沿知识库」不是一回事：

| 路径 | 用途 |
|------|------|
| **AGENTS.md** | Agent 角色与指令（定时提醒、heartbeat 等）。 |
| **TOOLS.md** | 工具使用说明（exec 安全、cron 等），给 agent 看的。 |
| **SOUL.md / USER.md / HEARTBEAT.md** | 其他模板，用于人格、用户信息、周期任务等。 |
| **memory/MEMORY.md, memory/HISTORY.md** | 长期记忆与历史日志。 |
| **skills/** | 放**自定义 skill**（每类一个子目录，内含 SKILL.md），agent 会按需加载。 |

**knowledge_base/** 是**本助手单独约定**的目录，用于 RAG 检索与订正；nanobot 默认不会创建它，需要你按上文自建并复制 docs/knowledge_base 下的文件进去。

### 4.4 目录结构（运行时 workspace 下）

```text
<workspace>/   # 默认 ~/.nanobot/workspace
  knowledge_base/
    README.md              # 从 docs/knowledge_base/ 复制
    facts.md               # 从 docs/knowledge_base/ 复制，既有事实
    uncertain/             # 待验证，需自建
      YYYY-MM-DD.md
    YYYY-MM-DD-agent-advances.md   # 可选：按日主库
  memory/
    rag/                   # RAG 持久化索引（自动建）
  inbox/
    frontier-YYYY-MM-DD-raw.md     # frontier_ingest 写入
```

### 4.5 内容维度（面向 Agent 开发工程师）

- **facts.md** 建议包含：本知识库用途说明；已知框架/协议/术语（如 ReAct、MCP、LangChain、CrewAI、Tool Use）；重要基准与论文名；简短时间线（如「截至 20XX 年 X 月，主流方向是…」）。
- **按日/按周进展**：日期、来源、摘要、与工程师相关的要点（技术选型/影响）。
- **uncertain/**：注明「待验证」、来源、内容摘要，便于后续人工或二次 RAG 时降权。

详见 `docs/knowledge_base/` 下的 README 与 facts 示例。

### 4.6 RAG 在本项目中的实现

- **工具**：`rag_index`（从 knowledge_base 下建索引）、`rag_query`（通用检索，可设 reliability）、**`rag_query_dedup`**（去重专用：固定 reliability=all、scope=concrete_only，默认 top_k=5、最大 10；**query 须根据本期摘要内容构造**，从摘要提取关键主题/实体，禁止写死「近期 Agent 进展」）。流程中在「订正」前读本期 summary，用摘要要点调用 **rag_query_dedup**，将本期摘要与 RAG 结果一起喂给 LLM。

#### RAG 名词解释（通俗版）

- **向量（Chroma）**：把每段文字变成一组数字（向量），语义相近的段落向量也相近。检索时用「当前问题」的向量去找**语义最接近**的段落，适合按「意思」找相关内容，而不是只按关键词。
- **BM25**：按**关键词**打分：哪些段落里包含查询词越多、越集中，分数越高。适合精确匹配框架名、论文名、缩写等。
- **Hybrid（向量 + BM25）**：同时做向量检索和 BM25 检索，两边按**排名归一化**后再加权合并（BM25 权重大于向量），避免分数量纲不一致。
- **可靠性分级**：知识库里有的来自 facts.md（可靠）、有的来自 uncertain/*.md（待验证）。检索时可设 `reliable_only`（只看可靠库）、`prefer_reliable`（可靠加权）、`all`（不区分）。**去重**用 rag_query_dedup（固定 reliability=all + scope=concrete_only）：只查、只返回具体事实（可靠+待验证），general 不参与。**订正**用 rag_query(reliable_only)：取可靠库的 具体事实+general（不含待验证），结果**按相关性排序**（字面/关键词匹配优先，general 降权），不强制具体事实条靠前。

#### 是否需要单独安装向量数据库？

**不需要。** RAG 使用的向量库是 **Chroma**，以 **嵌入式** 方式运行：随 nanobot 进程一起启动，数据写在 `workspace/memory/rag/` 目录下，**无需单独安装或启动数据库服务**。

- 安装：`pip install 'nanobot-ai[rag]'` 会装上 chromadb、rank-bm25、sentence-transformers（zsh 下需加引号）。**若从源码/本地开发安装**，需带 extra：`pip install -e '.[rag]'`。
- **若上述方式装不上或环境里没有这三个包**：可**手动安装**：`pip install chromadb rank-bm25 sentence-transformers`（PyPI 包名是 `rank-bm25` 带连字符），再运行 nanobot 即可使用 RAG。
- Embedding 二选一：**OpenAI API**（在 config 里配 `tools.rag.apiKey`、`embedding_model`）或 **sentence-transformers 本地模型**（不配 apiKey 时自动用，首次会下载模型文件）。
- 总结：**不用单独下载/安装向量数据库**，装好上述依赖即可；索引在首次 `rag_index` 时自动建在 workspace 下。

---

## 五、流程由 Skills 管理；Subagents 仅在不并行就不够时用

### 5.1 原则：主 agent 按 skill 分阶段执行即可

- **流程与规范**：全部写在 **nanobot skill**（`nanobot/skills/agent-frontier/SKILL.md`）中。Cron 或用户触发「按 agent-frontier 执行」后，**主 agent 按 skill 顺序执行**：**步骤 0 先调用 rag_ensure_initialized**（检查 chroma.sqlite3，无则初始化）→ frontier_ingest → 总结 → 订正 → 写回知识库、rag_index → 影响分析 → 推送。
- **不在框架写死流程**：步骤顺序、异常分支、何时结束等均由 Skill 描述；框架层（AgentLoop）不注入「步骤 N/7」提示或强制收尾/续跑指令，避免把具体任务流程写死在代码里。
- **不必强行用 Subagents**：若不需要并行、也没有「长任务阻塞网关」的顾虑，**只让主 agent 在不同阶段按 skill 做即可**，无需 spawn。Subagents 适合的场景见 5.2。

### 5.2 Subagents 适合什么场景

- **适合用**：某一步**耗时长**（如总结/订正要处理很多内容），你希望网关不阻塞、用户先得到「已开始，完成后会发结果」的反馈时，可把该步 **spawn** 给 subagent 在后台跑，跑完结果通过 bus 发回。
- **适合用**：有**明确并行需求**（如多路采集并行再汇总）时，可对多路分别 spawn。
- **不必用**：若任务量不大、顺序执行即可，**主 agent 按 skill 一步步执行即可**，无需 spawn。当前前沿流水线默认推荐**主 agent 一条龙执行**，只有在你确实需要「不阻塞」或「并行」时再考虑 spawn。

### 5.3 采集为什么是「工具」而不是 Agent

- 采集阶段只是**调用搜索 API + 抓取 URL 正文**，没有「选哪些 query、选哪些 URL」的复杂推理时，用 **工具** 即可完成。**先检查本地**是否已有本日 raw 文件，有则不再调用搜索；无或为空时，一次 **frontier_ingest** 按默认或传入的 queries 执行搜索、对每条 query 抓取前 N 个 URL、写入 `workspace/inbox/frontier-YYYY-MM-DD-raw.md`，每段带 `[N chars]`。**无需 LLM，也无须 spawn 子 agent**。
- 若将来需要「根据本周主题自适应生成 queries」，再考虑在采集前加一层 agent 生成 queries 后调 frontier_ingest。

### 5.4 功能模块划分（主 agent 顺序执行即可）

| 模块 | 职责 | 实现方式 | 说明 |
|------|------|----------|------|
| **采集** | 多 query 搜索 + 抓取，写 raw 文件 | **frontier_ingest 工具**（无 LLM） | 输出：inbox 下 raw 文件，每段标 [N chars]。 |
| **总结** | 分源摘要（超长先 summarize）+ 合并 | Agent 或 Subagent | 读 raw，按源处理，输出本期采集摘要。 |
| **订正** | **去重与订正分离**：步骤 3 rag_query_dedup（reliability=all，仅具体事实：可靠+待验证）；**4.1** read_file(summary)；**4.2 去重**：根据步骤 3 RAG + 摘要判断有无新进展，无则 message 结束；**4.3 订正**（仅当有新进展）：先 rag_query(reliable_only) 取可靠库 具体事实+general（不含待验证）→ 再输出订正结论与带【可靠】/【待验证】正文 → write_file(corrected)。 | Agent 或 Subagent | 去重需含待验证（避免重复写入）；订正不需待验证（仅可靠库参考）。RAG 索引 facts.md 与 uncertain/*.md。 |
| **影响分析** | 对工程师的影响与指导 | Agent 或 Subagent | 输入：订正后摘要；输出：展望正文。 |
| **写回与推送** | 可靠→knowledge_base/facts.md，待验证→uncertain/；rag_index（按路径打可信度）；message | 主 agent | 数据源为 facts 与 uncertain 目录下 .md。 |

主 agent 在**被 Cron 或用户消息触发**后：**第一步**先调 **rag_ensure_initialized**（检查 chroma.sqlite3，无则初始化），再调 **frontier_ingest**，再按 skill 做总结 → 订正（输出带【可靠】/【待验证】标签）→ 写回知识库、rag_index（先存知识）→ 影响分析 → message 推送。

### 5.5 Subagent 与 RAG（仅在 spawn 订正/总结时需要）

- 若你把「总结」或「订正」spawn 给 subagent，subagent 需要 rag_query（及 summarize）。当前实现已为 subagent 可选注册 RAG 与 summarize，spawn 的订正/总结子任务可直接使用。

---

## 六、触发方式：定时为主，无需用户发消息

- **主触发**：由 **Cron 定时任务** 在设定时间（如每天 9:00）自动执行。配置好 Cron 后，**到点就会跑，不需要用户发任何消息**。
- **可选手动**：若想立刻再跑一次，用户可发消息「执行 Agent 前沿周报 / 按 agent-frontier 执行」触发同一条流程。
- 实施时：在 Cron 的 `message` 里写「按 agent-frontier skill 执行 Agent 前沿周报并推送到当前对话」，agent 首次配置 Cron 时由用户说一句「每天 9 点按 agent-frontier 做周报发到这里」即可；之后每天 9 点自动触发，无需再说话。

## 七、完整流程小结

0. **RAG 初始化（第一步）**：主 agent 先调用 **rag_ensure_initialized()**；工具检查 `workspace/memory/rag/chroma.sqlite3` 是否存在，不存在则自动执行 rag_index 完成初始化。
1. **触发**：Cron 到点自动执行（或用户可选手动发消息触发）。
2. **采集**：主 agent **先检查本地**是否已有本日 `inbox/frontier-YYYY-MM-DD-raw.md`；若无或为空再调用 **frontier_ingest**（无 LLM）。frontier_ingest 内部也会检查：本日 raw 已存在则直接返回、不发起搜索。得到 raw 后每段标 `[N chars]`。
3. **总结**：主 agent（或 spawn 总结子任务）read_file raw，按 **Query/Source 分块**；对超过约 2000 字的块先调 **summarize**，再合并为「本期采集摘要」（总长约 4000 字内）。
4. **去重与订正（步骤 3 + 步骤 4）**：**步骤 3**：read_file 本期 summary，构造多个 query，依次分别调用 rag_query_dedup（reliability=all，仅具体事实：可靠+待验证都查，general 不参与），全部完毕才进入步骤 4。**步骤 4.1**：再 read_file(summary) 衔接。**步骤 4.2 去重**：根据步骤 3 RAG 与摘要由模型判断有无新进展；无新进展则 message 结束。**步骤 4.3 订正**（仅当 4.2 判定有新进展时）：先调用 rag_query(reliability=reliable_only) 取可靠库的 具体事实+general（不含待验证），再输出订正结论与带【可靠】/【待验证】的订正正文，write_file(corrected)。订正后摘要每条须带【可靠】或【待验证】。
5. **写回知识库、rag_index**：按订正摘要中的【可靠】/【待验证】标签分写：可靠→facts.md（append），待验证→uncertain/日期.md；再调用 rag_index（先存有用知识）。
6. **影响分析**：基于订正后摘要与已写回要点，生成**对 Agent 开发工程师的影响与指导**（即最后发给用户的 message 内容）。
7. **推送**：用 **message** 将「本周要点 + 对工程师的展望」发到当前 channel。

---

长内容与多源：通过 raw 文件中的 `[N chars]` 与 **summarize** 工具在步骤 2 处理；多源始终按「分源摘要再合并」。与上次重复：通过订正步骤的 RAG（rag_query_dedup）做**知识去重**（进展内容本地已有或语义等价即视为重复，非仅文字一致）。

## 八、实施与代码要点

### 8.1 文档与 Skill

- **本文档**：`docs/AGENT_FRONTIER_ASSISTANT_DESIGN.md`（本文件），作为设计与约定总览。
- **Skill**：`nanobot/skills/agent-frontier/SKILL.md`（仓库内 builtin），写清：数据源与关键词、模块划分、步骤 3 读摘要调用 RAG / 步骤 4 再读摘要并订正、知识库写入与 rag_index 约定等。
- **Skill 加载优先级**：先查 **workspace/skills/agent-frontier/SKILL.md**，若不存在则用 **builtin**。builtin 路径来自 `Path(__file__).parent.parent / "skills"`，即**当前运行进程所加载的 nanobot 包所在目录**下的 `skills`。若通过 **pip 安装**后运行（如 `pip install -e .` 或 `pip install nanobot-ai`），该目录通常是 **site-packages/nanobot/skills** 或 editable 安装的源码目录，**不是你正在编辑的仓库路径**，所以改仓库里的 `nanobot/skills/agent-frontier/SKILL.md` 可能不会生效。请看日志中的 `Skill agent-frontier loaded from builtin: <路径>`，若该路径不是你的仓库下的 `nanobot/skills`，请：重新安装（如 `pip install -e .` 从仓库根目录执行）、或从仓库以「当前目录即项目根」的方式运行（如 `PYTHONPATH=. nanobot run`），或把仓库内最新 `nanobot/skills/agent-frontier/SKILL.md` 复制到日志里显示的那个 builtin 路径下覆盖。
- **知识库示例**：`docs/knowledge_base/README.md`、`docs/knowledge_base/facts.md`，可复制到 workspace 使用。

### 8.2 Cron

- **首次配置**：用户在 Telegram 对 agent 说一次「每天 9 点按 agent-frontier 做周报发到这里」，agent 调用 cron add（deliver=true，channel=telegram，to=当前 chat_id）。**之后每天 9 点自动跑，无需再发消息。**
- 任务 message 写「按 agent-frontier skill 执行 Agent 前沿周报并推送到当前对话」即可。

### 8.3 已实现的代码

- **frontier_ingest**：采集工具，执行多 query 搜索 + 抓取，写 raw 到 inbox，每段标 `[N chars]`。主 agent 与（若需要）subagent 均可调用；采集阶段主 agent 调一次即可。
- **summarize**：长文本摘要工具，供主 agent 与 subagent 在「总结」时对超长块调用。
- **SubagentManager**：已支持可选 RAG 工具与 **summarize**，订正/总结子任务可用 rag_query 与 summarize。

### 8.4 可选：Cursor Rules

- 对 `**/agent-frontier/**`、`**/knowledge_base/**` 的编辑可加 Cursor 规则，要求符合本设计文档中的目录与命名约定。

按以上实施即可跑通：Cron 到点自动触发 → frontier_ingest 采集 → 总结（多源+summarize）→ 订正（RAG 去重）→ 写回知识库、rag_index → 影响分析 → message 推送。

---

## 九、具体需要做哪些工作才能让助手运行起来

按下面清单做完，助手才能实际跑通。

### 9.1 环境与依赖

1. **Python 与 nanobot**：已能正常运行 nanobot（如 `nanobot run` 或对应网关命令）。
2. **可选 RAG**：若要用知识库订正与检索，执行 `pip install 'nanobot-ai[rag]'`（zsh 下必须加引号）。若装不上或你从源码安装，可改用手动安装：`pip install chromadb rank-bm25 sentence-transformers`（三个包均在 PyPI，包名 rank-bm25 带连字符）。不需要 RAG 时可省略，agent 会退化为只读 knowledge_base 下的 md 文件（若存在）。

### 9.2 配置

3. **Workspace**：确认配置里 workspace 路径（默认 `~/.nanobot/workspace`）。若改过，后文路径都按你的 workspace 为准。
4. **Brave Search API Key**：采集依赖 Brave 搜索。在 `~/.nanobot/config.json` 的 `tools.web.search.apiKey` 中配置，或设置环境变量 `BRAVE_API_KEY`。未配置时 `frontier_ingest` 会报错，注意调用这个搜索引擎必须开代理，但是大模型调用通常有网络隔离，后续可以考虑支持国内直连的搜索API。
5. **RAG（可选）**：若装了 `[rag]`，可在 config 的 `tools.rag` 下配 `apiKey`、`embedding_model`（用 OpenAI），或不配而用本地 sentence-transformers。

### 9.3 知识库目录（必做）

6. **创建 knowledge_base**：在 **workspace 下**新建目录 `knowledge_base`（即默认 `~/.nanobot/workspace/knowledge_base`）。注意：**不是**项目里的 `docs/knowledge_base`，那是示例。
7. **复制示例进去**：把仓库里 **`docs/knowledge_base/README.md`** 和 **`docs/knowledge_base/facts.md`** 复制到上一步的 `knowledge_base/` 下。
8. **建 uncertain 目录**：在 `knowledge_base/` 下建子目录 `uncertain/`，用于存放待验证信息。

### 9.4 首次运行与 RAG 初始化（若用 RAG）

9. **本项目一开始没有 RAG 索引**（即 `workspace/memory/rag/chroma.sqlite3` 不存在）。**初始化方式**：在 knowledge_base 下已有 README.md、facts.md 后，**第一次**通过以下任一方式建索引即可：
   - **方式 A**：跑完整流水线，步骤 5 写回知识库后，步骤 6 调用 **rag_index()**，此时会创建 `memory/rag/` 目录并生成 chroma.sqlite3（即完成初始化）。
   - **方式 B**：在跑流水线前，先通过 agent 或 CLI 手动调用一次 **rag_index()**，提前建好索引。
10. **首次运行流水线时**：RAG 索引可能尚未建立，rag_query_dedup 可能返回「无相关片段」。**仍按步骤 3、4 订正**，仅用本期摘要即可；然后步骤 5→6→7。
   - **步骤 6 即初始化**：步骤 6 必须执行 rag_index()；首次执行会创建 chroma.sqlite3，后续执行会更新索引。**不可因「已有 chroma.sqlite3」就跳过步骤 6**——每次步骤 5 写回后都要 rag_index，否则新写入内容不会被检索到。
11. 在配置好 knowledge_base 且 facts.md 已就位后，可先手动调用一次 rag_index 提前初始化，或等流水线第一次跑到步骤 6 时由 agent 调用。

### 9.5 定时任务（Cron）

12. **创建 Cron 任务**：在 Telegram（或当前使用的 channel）对 agent 说一句，例如：「每天 9 点按 agent-frontier skill 执行 Agent 前沿周报并推送到当前对话」。由 agent 调用 cron add，`deliver: true`，`channel`/`to` 指向当前会话。之后到点自动执行，无需再发消息。
13. 若不用 Cron，可随时发消息「按 agent-frontier 执行前沿周报」手动跑一次。

### 9.6 推送目标

14. 若希望总结推到 **Telegram**，需已配置好 Telegram channel，且 Cron 的 `channel`/`to` 为对应会话。写回与推送由主 agent 用 **message** 工具完成，无需额外配置。

---

**检查清单小结**：Brave API Key、workspace 下 knowledge_base（含 README+facts+uncertain）、可选 RAG 安装与配置、Cron 创建。完成即可运行。
