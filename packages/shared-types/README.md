# @xgzh/shared-types

跨端共享 TypeScript 类型，由后端 OpenAPI 自动生成。

## 生成方式（待实现）

```bash
# Sprint 1 之后实现
pnpm --filter @xgzh/shared-types gen
# 内部执行：
#   1. 后端启动 → curl http://localhost:8000/openapi.json
#   2. openapi-typescript 转 TS 类型 → src/index.ts
```

## 使用

```ts
import type { paths, components } from '@xgzh/shared-types'

type IPOItem = components['schemas']['IPOItem']
```
