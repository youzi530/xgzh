# @xgzh/prompts

版本化的 Prompt 库（System Prompt / Tool Description / 评测题等）。

## 命名规约

```
packages/prompts/<module>/<name>.v{n}.md
```

例：

- `agent/diagnose.v1.md` — 新股诊断主提示
- `agent/tldr.v1.md` — 文章 TL;DR 摘要
- `crs/assessment.v1.md` — CRS 身份判定问卷

## 修改规则（详见 `.cursor/rules/30-ai-agent.mdc`）

- 任何变更 = 新版本号 + CHANGELOG，旧版本保留
- 必须包含中立护栏（"严禁建议买入/必涨"等）
- 通过 `packages/eval/` 离线评测后再上线
