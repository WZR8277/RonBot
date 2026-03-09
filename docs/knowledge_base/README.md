# Agent 前沿知识库

本目录供「Agent 前沿信息助手」定时任务使用：存放可靠事实与待验证信息，供 RAG/总结时检索与更新。

## 约定

- **可靠信息**：写入 `facts.md` 或按日 `YYYY-MM-DD-agent-advances.md`。格式需含日期、来源、摘要。
- **存疑/待验证**：写入 `uncertain/YYYY-MM-DD.md`，注明「待验证」与来源。
- 只追加不删除；文件过大时可人工归档。

## 目录结构

```
knowledge_base/
  README.md     # 本说明
  facts.md      # 长期可靠事实与时间线
  uncertain/    # 待验证信息
```

初始化时可将本文件与 `facts.md` 复制到 workspace 下 `knowledge_base/`。
