# Agent/AI 前沿 — 可靠事实库【general】

本文件为既有事实与时间线，供「Agent 前沿信息助手」RAG 检索与订正时去重、补全，并支撑对 Agent 开发工程师的影响与展望分析。流水线会将新可靠进展**追加**到本文件或按日文件；存疑内容写入 `uncertain/YYYY-MM-DD.md`。【general】

---

## 对工程师有价值的信息维度

- **框架与协议**：MCP、LangGraph、CrewAI、AutoGen、ReAct、Tool Use / Function Calling【general】
- **基准与评估**：SWE-bench、AgentBench、WebArena、Agent 能力维度与排行榜【general】
- **模型与 API**：推理模型、长上下文、多模态、与 Agent 强相关的 API 更新【general】
- **工程实践**：可观测性、安全（提示注入等）、成本控制、部署模式【general】
- **论文与开源**：重要论文与高星开源 Agent 项目【general】
- **生态与招聘**：岗位中的 Agent 技能需求、社区热点【general】

---

## 已知术语与范式

- **ReAct（Reasoning + Acting）**：推理与行动交替的范式，LLM 先生成推理步骤再决定调用哪些工具，适合需要多步决策的 Agent。【general】
- **Chain-of-Thought（CoT）**：让模型显式输出推理链，提升复杂推理与数学/代码任务表现。【general】
- **Tool Use / Function Calling**：模型输出结构化工具调用（名称+参数），由运行时执行并回传结果，是当前主流 Agent 与外部系统对接方式。【general】
- **MCP (Model Context Protocol)**：Anthropic 等推动的工具与上下文协议，用于标准化 Agent 与工具、数据源的对接，便于复用与组合。【general】
- **RAG（Retrieval-Augmented Generation）**：检索增强生成，先从不稳定/长文档中检索相关片段再喂给 LLM，常用于知识库问答与 Agent 的「读文档」能力。【general】
- **Agentic workflow**：以 Agent 为中心的工作流，由 LLM 做规划与调度、多步调用工具并迭代，区别于单次 prompt-completion。【general】

---

## 主流框架与库（编排 / 工具 / RAG）

- **LangChain**：链式编排、Agent、Tool、RAG 与多种集成，生态大、版本迭代快。【general】
- **LlamaIndex**：侧重数据索引与 RAG，对结构化/非结构化数据接入与查询优化支持多。【general】
- **CrewAI**：多 Agent 协作框架，强调角色与任务分工、Agent 间协作。【general】
- **AutoGen**：微软开源的多 Agent 对话与协作框架，支持人机混入与代码执行。【general】
- **Semantic Kernel**：微软的 AI 编排 SDK，插件与规划能力，与 Azure 集成深。【general】
- **Haystack**：侧重 NLP/RAG 流水线，文档检索与问答场景。【general】
- **DSPy**：用「声明式」方式定义 prompt 与检索，强调可优化与可复现。【general】
- **LangGraph**：基于图的 Agent 工作流，支持循环、分支与状态，适合复杂多步逻辑。【general】

---

## 基准与评估

- **SWE-bench**：用真实 GitHub issue 评估模型修 bug 能力，需读 repo、改代码、通过测试。【general】
- **AgentBench**：多环境（OS、数据库、浏览器等）下评估 Agent 的推理与工具使用。【general】
- **WebArena**：基于网页环境的 Agent 任务，需要理解页面、点击、填表等。【general】
- **GAIA**：通用 AI 助手基准，需多步推理与工具使用。【general】
- **HumanEval / MBPP**：代码生成与通过率评估，常与 Agent 代码能力一起看。【general】
- **MT-Bench 等**：对话与指令遵循评估，与 Agent 的「理解任务」相关。【general】

---

## 模型与 API 侧（与 Agent 强相关）

- **Function calling / tool use**：OpenAI、Anthropic、Google、国内多家模型均已支持结构化工具调用，是 Agent 落地的标配能力。【general】
- **长上下文**：128K、200K、1M token 级窗口逐渐普及，影响「一次可读多少文档、多少轮对话」的架构选择。【general】
- **推理/思考模型**：Claude 的 extended thinking、DeepSeek-R1、OpenAI o1 等，强调复杂推理与链式思考，与 Agent 的规划与纠错能力相关。【general】
- **多模态**：视觉+语言模型用于「看界面、看文档图」类 Agent 任务，如 UI 自动化、文档理解。【general】

---

## 工程实践（部署、可观测、安全、成本）

- **可观测性**：Agent 调用链长、依赖外部工具，需要 trace（每步输入输出、延迟、错误）、日志与可选监控大屏，便于排障与优化。【general】
- **提示注入与越权**：用户或外部内容可能试图篡改 system prompt 或诱导模型执行不当操作，需要输入清洗、权限边界与审计。【general】
- **成本控制**：多轮对话与长上下文带来 token 与 API 成本，需控制单次任务轮数、缓存与裁剪策略。【general】
- **部署形态**：单机脚本、常驻服务、Serverless、与现有 CI/CD 或 IM 集成等，影响选型与运维方式。【general】

---

## 重要论文与开源项目（代表性）

- **ReAct: Synergizing Reasoning and Acting in Language Models**：ReAct 范式提出，推理与行动交替。【general】
- **Chain-of-Thought Prompting**：CoT 显式推理链，提升推理类任务。【general】
- **Tool Learning / Toolformer**：让模型学习何时、如何调用工具。【general】
- **开源 Agent 项目**：除上述框架外，GitHub 上常见「LLM + 工具 + 某垂直场景」的 repo，如客服、代码助手、数据分析等，选型时可看 star、维护状态与与己方技术栈的匹配度。【general】

---

## 时间线（由流水线与人工追加）

- 当前主流方向包括：多步推理与规划、多模态 Agent、与 MCP 等工具协议集成、Agent 可观测与成本控制、长上下文与推理模型在 Agent 中的使用。【general】
- 框架层：LangChain/LangGraph、CrewAI、AutoGen、Semantic Kernel 等持续迭代；协议层 MCP 等逐步被更多工具与平台采纳。【general】
- 评估层：SWE-bench、AgentBench、WebArena、GAIA 等成为常见对比基准；业界关注通过率、任务覆盖与可复现性。【general】
- （以下由定时任务或人工追加可靠进展，格式：日期、来源、摘要、对工程师的要点。）【general】