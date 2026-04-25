# xgzh-mp

XGZH (新股智汇) UniApp 客户端 — 第一刀。

## 功能

- 首页：港股 / A 股近期 IPO 列表
- 详情页：基础信息 + 「AI 一键诊断」入口
- AI 诊断页：DeepSeek-V3 流式输出（SSE）

## 启动

### 方式 1：HBuilderX（推荐用于小程序 / App）

1. 用 HBuilderX 直接打开 `apps/mp` 目录
2. 顶部菜单 `运行 → 运行到小程序模拟器 → 微信开发者工具`
3. 自动构建到 `unpackage/dist/dev/mp-weixin/`，微信开发者工具会被唤起

### 方式 2：CLI（H5 调试最快）

```bash
cd apps/mp
pnpm install
pnpm dev:h5
# 访问 http://localhost:5173
```

注意：H5 模式下 `vite dev server` 会把 `/api` 代理到 `http://localhost:8000`（见 `manifest.json` 的 `h5.devServer.proxy`）。

## 目录结构

```
apps/mp/
├── pages/
│   ├── index/index.vue       # 首页（IPO 列表）
│   └── ipo/
│       ├── detail.vue        # 详情页
│       └── agent.vue         # AI 诊断页
├── api/
│   ├── ipo.ts                # IPO 接口封装
│   └── agent.ts              # Agent 流式接口
├── utils/
│   ├── request.ts            # uni.request 封装
│   └── sse.ts                # 跨端 SSE 流式接收
├── App.vue / main.ts / pages.json / manifest.json
└── tsconfig.json / vite.config.ts
```

## 已遵守的合规约束

- AI 输出页顶部固定免责条 "AI 输出仅供参考，不构成投资建议"
- 所有金融数值使用专门字段，未来涉及计算时用 `big.js`（已在 deps）
- 详情页底部固定数据来源声明

详细规范见 `.cursor/rules/20-frontend-uniapp.mdc`。
