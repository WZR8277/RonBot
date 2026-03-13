# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## cron — Scheduled Reminders

- Please refer to cron skill for usage.

## frontier_ingest — 前沿周报采集

- **仅**在用户明确要求「前沿周报」「Agent 周报」或 Cron/系统触发时调用。
- 不要因用户打招呼、闲聊或无关请求而调用。
- 需要**中文社区/博客**来源时：传 `search_lang="zh"`，可选 `country="CN"`；否则默认英文结果。
