---
name: agent-frontier
description: Agent/AI frontier digest (2026). ONLY when user asks 前沿周报 or Cron triggers. You MUST execute the steps below in order; step 0 first, then 1→2→3→4→5→6→7; your next reply MUST be the required tool call(s), not natural language, until step 7 message is sent.
---

# Agent 前沿信息助手（2026）— 严格按步骤执行

本任务为**步骤 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7** 顺序执行。若某步因错误无法完成，允许用 message 说明原因并结束。

---

## 步骤 0：检查并初始化 RAG 索引（第一步必做）

- **动作**：**首先**调用 **rag_ensure_initialized()**（无参数）。该工具会检查 `workspace/memory/rag/chroma.sqlite3` 是否存在：若**不存在**则自动调用 rag_index 建索引（即初始化）；若**已存在**则直接返回，不重复建。
- **完成条件**：工具返回（无论「已初始化」或「刚完成初始化」）。
- **下一步必须**：进入步骤 1（采集）。

---

## 步骤 1：采集

- **动作**：**先检查本地**是否已有本日 raw 文件（可先 read_file 或 list_dir `inbox` 看是否存在 `inbox/frontier-YYYY-MM-DD-raw.md`）。若**已存在且非空**，则**跳过采集**，直接进入步骤 2。若**不存在或为空**，再调用 **frontier_ingest**（参数：date=本日 YYYY-MM-DD，queries 技术向，可选 search_lang=zh）。frontier_ingest 内部也会检查：若本日 raw 已存在则直接返回「已存在，跳过采集」，不发起搜索。
- **完成条件**：raw 文件存在且可读。
- **若 frontier_ingest 返回错误**：重试 1 次；仍失败则用 message 返回「步骤1失败：xxx」并结束。
- **下一步必须**：调用 read_file 读 `inbox/frontier-YYYY-MM-DD-raw.md`，进入步骤 2（不可输出「已采集完成」等收尾语后结束）。

---

## 步骤 2：总结

- **动作**：read_file(raw) 后，对过长块调用 **summarize**，再将「本期采集摘要」**write_file** 到 `inbox/frontier-YYYY-MM-DD-summary.md`（不覆盖 raw）。
- **完成条件**：`inbox/frontier-YYYY-MM-DD-summary.md` 已写入。
- **若 raw 为空**：用 message 返回「无采集数据」并结束。
- **下一步**：进入步骤 3。

---

## 步骤 3：读摘要 → 构造多个 query → 依次分别查询（去重用，仅查具体事实），全部完毕再进入步骤 4

- **动作**：
  1. **读本期摘要**：read_file(`inbox/frontier-YYYY-MM-DD-summary.md`)。
  2. **根据摘要构造多个查询**：摘要可能有多条要点，从摘要中**提取多个关键词/主题**（框架名、协议、benchmark 等），每个对应一个 **query**。**禁止写死 query（如「近期 Agent 进展」）**，必须用摘要里的实际要点。酌情 2～5 个 query 即可。
  3. **依次分别调用 rag_query_dedup**：对每个 query **各调用一次** rag_query_dedup（query=该关键词/主题，top_k=3 或不传）。**全部查询都完成**后，步骤 3 才算结束。可在一轮内同时发起多次调用（不同 query），框架会**并行执行**，全部返回后再进入步骤 4；也可多轮依次调用，直到所有关键词都查完。**本步仅用 rag_query_dedup**，禁止用 rag_query。
- **只查具体事实**：rag_query_dedup 只返回**具体事实**（【可靠】进展条），不返回 general（框架、评估、工程实践等基础提示类内容）。
- **完成条件**：已读 summary，已用「基于摘要构造的**全部** query」**分别**调用 rag_query_dedup，且**每次**都**成功**返回（有结果或「无相关片段」等正常返回均可）。
- **失败则返回**：若某次调用返回 **Error** 或无法继续，用 message 说明并结束，不执行步骤 4 及后续。
- **下一步**：全部查询成功 → 进入步骤 4。

---

## 步骤 4：去重与订正（两件事，按顺序做）

**去重**和**订正**是两件事：先根据步骤 3 的 RAG 结果做去重判断；**仅当有新进展时**才再查全量并做订正、写 corrected。

### 步骤 4.1：再读本期摘要（衔接）

- **动作**：调用 **read_file** 读取 `inbox/frontier-YYYY-MM-DD-summary.md`（若步骤 3 已读过可沿用，否则本步必读）。
- **完成条件**：手头有本期摘要与步骤 3 的 RAG 返回全文（仅具体事实）。
- **下一步**：进入 4.2 做去重。

### 步骤 4.2：去重（仅判断有无新进展，不订正、不写文件）

- **动作**：根据**本期摘要**与**步骤 3 的 RAG 结果**（仅具体事实），由模型逐条对照：摘要里每一条在 RAG 中是否已有同一条事实（同一产品/结论/事件或同义）。**RAG 已写的 → 重复。** 仅 RAG 全文中都找不到的才标为新进展。在**内部**得出结论：本期有无新进展（不在此步发 message）。
- **无新进展**：输出「经对比，本期无新进展，与知识库重复」，并 **message** 告知用户，**结束**（不调用 rag_query、不订正、不 write_file）。
- **有至少一条新进展**：**禁止**在步骤 4.2 调用 message；不得发出「经对比，本期有新进展」或类似内容。应**直接**进入步骤 4.3。
- **完成条件**：已给出有无新进展的结论；无新进展时已 message 结束；有新进展时**不 message、直接 4.3**。

### 步骤 4.3：订正（仅当 4.2 判定有新进展时执行）

- **动作**：
  1. **先查可靠库**：调用 **rag_query**（reliability=reliable_only），根据本期摘要中的要点构造若干个 query，获取**可靠事实库**中的具体事实 + general（**不含待验证 uncertain**），供订正时参考。
  2. **再订正**：根据本期摘要 + 可靠库全量 RAG 结果，由模型分析并输出**订正结论**（一句以「经对比」开头）与**带【可靠】/【待验证】的订正正文**（每条须带标签：权威/一手→【可靠】，存疑/简约源→【待验证】）；**先**在回复中写出上述内容，**再**调用 write_file 将订正正文写入 `inbox/frontier-YYYY-MM-DD-corrected.md`。
- **完成条件**：已调用 rag_query 拿到全量、已输出订正结论与带标签的订正正文、已 write_file(corrected)。
- **下一步**：进入步骤 5。

---

## 步骤 5：写回知识库（必做两处）

- **动作**：根据订正文件 **corrected 中的二元标签** 分写：
  - 带 **【可靠】** 的条目 → **write_file** 到 `knowledge_base/facts.md`，**必须传 append=true**，不得覆盖原文件
  - 带 **【待验证】** 的条目 → **write_file** 到 `knowledge_base/uncertain/YYYY-MM-DD.md`
- **标签必须保留**：写入 facts.md 或 uncertain 的**每条正文须保留文内【可靠】或【待验证】标记**（即整条照抄 corrected 中带标签的原文），不得在写回时去掉标签；否则后续建索引无法识别为具体事实，检索会失效。
- **完成条件**：上述两处均已 write_file（路径均相对 workspace，不要写 workspace/ 前缀）。分写时必须严格按 corrected 中的 【可靠】/【待验证】 标签，不要漏标或混写。
- **数据源**：数据源为配置中的 **knowledge_base 目录**下的 **facts.md**（可靠事实）与 **uncertain（未验证）目录** 下的 .md 文件。写回即写入这两处：可靠→facts.md，待验证→uncertain/日期.md。
- **下一步必须**：调用 **rag_index()**（无参数），进入步骤 6。

---

## 步骤 6：更新索引

- **动作**：**必做** — 调用 **rag_index()**，无参数。索引对象为 knowledge_base 目录下的 **facts.md** 与 **uncertain/ 目录** 中的 .md；**建索引时会按路径分别打可信度标签**。
- **不可跳过**：步骤 5 写回 facts.md / uncertain 后，**必须**在本步调用 rag_index；否则新写入的进展（如 OpenClaw、GPT-5.4、Kimi 等）**不会被检索到**，后续订正与检索会一直看不到新内容。
- **完成条件**：rag_index 调用成功（或返回 Success/空库提示）。返回信息中会包含索引路径与 sources，可核对是否包含 facts.md。
- **若返回 Error**：记录后仍进入步骤 7，用 message 推送时可注明「索引更新失败，请稍后手动 rag_index」。
- **下一步必须**：进入步骤 7，生成影响分析并调用 message。

---

## 步骤 7：影响分析 + 推送

- **动作**：基于订正摘要分析并撰写「本周要点（3–5 条）+ 对 Agent 开发工程师的展望」（技术栈/协议/基准/可落地建议/风险与选型）；然后**只调用一次** **message**，content 为上述分析得到的正文。
- **完成条件**：message 已调用，用户收到内容。
- **禁止**：不得用你的自然语言回复先发出周报再「请稍等」；用户只会收到 message 的那一条。不得只发新闻式总结，必须含「对工程师的展望」。

---

## 工具约束摘要

| 工具 | 本流程中要求 |
|------|----------------|
| **rag_ensure_initialized** | 步骤 0 必须首先调用 1 次，无参数 |
| frontier_ingest | 步骤 1 最多调用 1 次（本日无 raw 时），参数含 date |
| read_file | 步骤 2 读 raw；步骤 3 读本期 summary 后构造 query 并调用 rag_query_dedup；**步骤 4.1** 再读本期 summary（衔接去重）。 |
| summarize | 步骤 2 中对超长块调用，次数按需 |
| write_file | 步骤 2 写 summary；**步骤 4.3** 订正成功后写 corrected（**含【可靠】/【待验证】**）；步骤 5 按标签写两处：facts.md（**append=true**）、uncertain/日期.md；**写回时正文须保留【可靠】/【待验证】标记**。 |
| **rag_query_dedup** | 步骤 3 去重用：仅返回**具体事实**。步骤 4.2 根据该结果做**去重**（判断有无新进展），不订正。 |
| **rag_query** | **步骤 4.3** 仅当 4.2 判定**有新进展**时调用：reliability=reliable_only，取可靠库的 具体事实+general（不含待验证），供订正参考。 |
| rag_index | 步骤 6 必须调用 1 次，无参数 |
| message | 步骤 3 查询失败时 message 结束；**步骤 4.2 仅无新进展时** message 结束（**有新进展时禁止 message**，直接 4.3）；步骤 7 调用 1 次推送要点+展望 |

---

## 异常与边界

- 步骤 0：第一步调用 rag_ensure_initialized。
- 本日 raw 已存在：步骤 1 跳过，直接步骤 2。
- 本日 summary 已存在：步骤 2 可跳过，直接步骤 3。
- 步骤 3：读本期 summary → 根据摘要构造**多个** query → **依次分别**调用 rag_query_dedup（每个 query 一次），**全部查询完毕**才进入步骤 4；可一轮内多次调用、并行执行。任一次**失败**则 message 说明并结束。
- 步骤 4.1：read_file(summary)，进入 4.2。步骤 4.2：仅做去重（根据步骤 3 RAG + 摘要判断有无新进展）；无新进展 → message 结束；有新进展 → 进入 4.3。步骤 4.3：先 rag_query 全量，再订正并 write_file(corrected)，然后步骤 5。
- 订正带标签：corrected 中每条须有【可靠】或【待验证】，步骤 5 按标签分写。
- 某步失败：可用 message 说明并结束。
- 已执行过 rag_query_dedup 或 write_file(corrected)：不再调用 frontier_ingest，只继续后续步骤。
