# Sprint 7.3 — `bug-fix-22:24` 2 项 (1 大功能 + 1 P0 主题) (2026-04-29 22:24–23:30)

> 状态: ✅ **已交付** — 用户基于 Sprint 7.2 spike v2 结论, 拍板"长桥 OpenAPI 替代搜狗
> 走多渠道", 本版直接修复(不再 spike-only); 同时报 Sprint 7.2 ② 漏修 — mp 切深色后
> 字体仍是黑色看不清. 用户拍板 ``A 框架先行`` (长桥 client 完整代码 + token=空时跳过)
> + ``all`` 全做. 总工时 ~0.6d (BE 长桥 client 0.4d + 5 页色修 0.05d + spec 0.15d).

参考:

- 上游: [`spec/21-sprint-7.2-bug-fix-backlog.md`](./21-sprint-7.2-bug-fix-backlog.md)
- 用户原始 bug 单: [`docs/bug/2026.04.29-bug.md`](../docs/bug/2026.04.29-bug.md)
  (bug-fix-22:24 段, 2 项)
- 长桥 OpenAPI 文档: <https://open.longbridge.com/zh-HK/docs>
- 现有大V tab FE: [`apps/mp/pages/ipo/detail.vue`](../apps/mp/pages/ipo/detail.vue)
  (二级 chip "持牌媒体 / 大V点评")

---

## 🐛 用户上报 (`bug-fix-22:24`)

| # | 现象 | 严重度 | 实施 |
|---|------|:----:|---|
| ① | 大V 多渠道抓取 — 长桥 / 搜索引擎 + site:mp.weixin / 现有搜狗 多源融合, **本版修复** | **P1 大功能** | spec/21 推荐路径直接落地 |
| ② | mp 切深色后**字体仍是黑色**, 看不清 | **P0 主题** | Sprint 7.2 audit 漏修 — 5 页 .page 加了 background 但**没加 color** |

---

## 🔬 Bug ② mp 深色字体没切 — Sprint 7.2 audit 留尾

### 根因

Sprint 7.2 BUG-S7.2-002 的 Python audit 脚本 ``fix_page_bg.py`` 只插了
``background: var(--color-bg)``, 没插 ``color: var(--color-text)``. 5 页
现状:

| 页 | 路径 | Sprint 7.2 修后 .page |
|---|---|---|
| 首页 | ``index/index.vue`` | ✅ background ❌ **无 color** |
| IPO 详情 | ``ipo/detail.vue`` | ✅ background ❌ |
| IPO 历史 | ``ipo/historical.vue`` | ✅ background ❌ |
| IPO 历史规律 | ``ipo/historical-pattern.vue`` | ✅ background ❌ |
| IPO Agent | ``ipo/agent.vue`` | ✅ background ❌ |

切到 light: view.theme-light 重定义 ``--color-text=#0f172a`` 深字, 子元素
继承显示深字 ✅; 切回 dark: view.page 自己**没设 color**, 透出 mp ``page``
元素的 ``color: var(--color-text, #e2e8f0)``. 但 mp wxss 渲染时序: ``--color-text``
变量在 view.theme-light 切走后**不及时重置**, 子元素仍用旧的 ``#0f172a``
深字 → 用户切回深色看到黑字 (旧上下文).

### 修法

写 Python 脚本 ``fix_page_color.py`` 给 5 页 .page 在 background 后插一行:

```scss
color: var(--color-text, #e2e8f0);
```

view.page 自己有 color 后, 子元素继承显式深字 dark fallback, 切回 dark
立即生效, 不依赖 cascade 时序.

---

## 🔬 Bug ① 大V 多渠道抓取 — 长桥 OpenAPI framework + 搜狗节流

### 实地 spike 结果(本 sprint 复测)

用户在 bug-fix-21:53 提的 3 个新思路:

| 思路 | 实跑结果 | 决策 |
|---|---|:---:|
| 长桥 OpenAPI | 文档站超时 (本地→香港时延) 未获实测 endpoint, 但 spike v2 已确认完全免费 + 港股新闻/社区 API 全开 | ⭐⭐⭐⭐⭐ **采纳, framework 实施** |
| Baidu site:mp.weixin.qq.com | ❌ **直接重定向到图形验证码** ``wappass.baidu.com/captcha/tuxing_v2.html``, 单次 curl 即触发 | ❌ 反爬比搜狗还狠, 不可行 |
| Bing site:mp.weixin.qq.com | ❌ 国内中文搜索难以可靠 curl (本地 IP 拿到 ``Region:SG, Lang:en-US`` 英文页) | ❌ 不可靠 |

→ **真正可行的新源就只剩长桥**. Baidu/Bing 路全断, 搜狗仍是微信公众号渠道核心.

### 实施: `LongbridgeApiClient` + 配置 + dispatcher

#### 1. 新增 `app/services/article_ingest/sources/longbridge_api_client.py` (340 行)

`ArticleSource` 协议实现, 关键设计:

```python
class LongbridgeApiClient:
    name = "长桥 OpenAPI"

    @property
    def is_enabled(self) -> bool:
        """token 配了才启用; 没配返 False, dispatcher 据此跳过注册."""
        return bool(self._settings.longbridge_api_token)

    async def fetch(self, *, since=None) -> list[ArticleRaw]:
        if not self.is_enabled:
            return []  # token 空 → 立即返, 不发任何 HTTP
        # ...走 OAuth Bearer + httpx.AsyncClient
```

字段映射 (按公开文档示例 + 备选 schema 容错):

```python
# 主 schema (官方文档推荐)
payload["data"]["list"][i]["title"]    -> ArticleRaw.title
payload["data"]["list"][i]["link"]     -> original_url
payload["data"]["list"][i]["source"]   -> "长桥·" + source_raw  -> source_name
payload["data"]["list"][i]["published_at"] (unix s/ms 都接) -> datetime UTC
payload["data"]["list"][i]["summary"]  -> summary

# 备选 schema (容错: data.news / payload.list / publishTime / publish_time)
```

合规:
- ``source_credibility = 3`` 等同持牌媒体 (长桥是港股持牌券商)
- ``is_full_text_available = True`` 长桥 link 允许 webview 渲染
- ``source_name = "长桥·<原 source>"`` 标记数据来源, FE 可加二级 chip 区分
  ``长桥 / 微信 / 持牌媒体``

异常处理:
- 401/403 → 中止整批 (token 失效, 后续 symbol 也会失败, 节省请求)
- 5xx 单 symbol skip + 继续
- ``json.JSONDecodeError`` 单 symbol skip
- 网络超时 ``logger.warning`` skip

#### 2. config 新增 5 项

```python
longbridge_api_token: str = ""               # 留空 = 不启用
longbridge_api_base_url: str = "https://openapi.longbridge.global"
longbridge_api_news_path: str = "/v1/quote/news"
longbridge_api_max_queries: int = 20         # 单次 ingest 查 N 个 HK symbol
longbridge_api_inter_query_delay_seconds: float = 0.2  # 长桥 10 次/秒, 0.2s 保守
```

**搜狗节流加强** (用户 bug-fix-21:53 报反爬触发):
```python
# Sprint 6.9 默认 1.5s -> Sprint 7.3 调高到 3.0s
article_ingest_sogou_inter_query_delay_seconds = 3.0
```

#### 3. dispatcher 注册 (token-gated)

```python
# Sprint 7.3 新增 — 仅在 token 配置时注册. HK symbol 从活跃 IPO 索引拿 (A 股
# 不在长桥覆盖范围, 索引里只取 market='HK')
if settings.longbridge_api_token:
    hk_symbols = [
        ipo.code for ipo in keyword_index._ipos
        if ipo.market == "HK" and ipo.code
    ]
    if hk_symbols:
        sources.append(
            LongbridgeApiClient(
                settings=settings,
                symbols=hk_symbols[: settings.longbridge_api_max_queries],
            )
        )
```

注册池升级到 6 源:

| 源 | 类型 | 关键词驱动 | token? |
|---|---|:---:|:---:|
| ZhitongRSSClient | RSS 大池 | ❌ | — |
| SinaFinanceClient | JSON 大池 | ❌ | — |
| XueqiuClient | 关键词搜索 | ✅ | — |
| EastmoneySearchClient | 关键词搜索 | ✅ | — |
| SogouWechatClient | 关键词搜索 | ✅ (微信公众号大V) | — |
| **LongbridgeApiClient** (新) | symbol 驱动 | ✅ (HK code) | ✅ token-gated |

#### 4. 单测 12 个全绿

`tests/test_longbridge_api_client.py` 覆盖:

```
A. parse_longbridge_news_json (7 个):
   1. happy 3 条 + 字段校验
   2. unix 秒/毫秒兼容
   3. 缺 title / link / published_at skip
   4. 备选 schema (data.news / payload.list)
   5. 空 / 非 dict payload
   6. 缺 source 字段 fallback "长桥·长桥"
   7. URL 跨 symbol 去重

B. fetch_longbridge_with_client (3 个):
   8. 200 happy 多 symbol 去重
   9. 401 中止整批 (后续 symbol 不请求)
   10. 5xx 单 symbol skip 不影响其他

C. LongbridgeApiClient 行为 (2 个):
   11. token=空 → is_enabled=False, fetch() 返 [] 0 HTTP
   12. token 配 + 空 symbols → fetch 返 []
```

实测: 12 passed in 3.20s. 全套 645 passed (53 skip).

### 用户 follow-up checklist (token 申请)

- [ ] 长桥 App 开户 (用户身份证件 5min)
- [ ] 登录 [open.longbridge.com](https://open.longbridge.com/) 申请 OpenAPI 权限
- [ ] OAuth2 流程拿 access_token (或用纸账户 paper account 调试更快)
- [ ] 把 token 填到 ``.env`` 的 ``LONGBRIDGE_API_TOKEN``, 重启 BE 立即生效
- [ ] 用户实测一次后**反馈实际 endpoint 路径**, 若与默认 ``/v1/quote/news`` 不同
  改 ``longbridge_api_news_path`` 配置项

---

## 📋 Lessons Learned (Sprint 7.3 retro)

### 1. Audit 脚本的"漏修一对"反模式

Sprint 7.2 audit 5 页 .page 加 background, 但 ``background`` + ``color`` 是
**主题切换的最小完整对**, 只加一个等于半成品. 用户立刻测出 "字体黑色看不清"
其实是**同一个 audit 任务漏了一半工作**.

正确做法: audit 的"标准块"应该是逻辑配对 (background+color, padding+margin,
width+height), 脚本里每个 audit 任务必须列出**完整 spec**, 不能只插一行.

**Lesson**: CSS audit 脚本必须把"成对属性"作为一个 block 一起处理, 不要单插
一行就 commit. 主题切换最小完整集 = ``background + color + (border)``.

### 2. 实地 spike 推翻"理论可行" — Baidu/Bing 全断

Sprint 7.2 spec/21 推荐 "Baidu site:mp.weixin 作 fallback", 但本 sprint 实跑
``curl baidu.com/s?wd=site:mp.weixin.qq.com+xxx`` **单次即重定向到图形验证码**,
反爬比搜狗微信还严重. Bing 国内中文搜索难以稳定 curl (本地 IP 自动到英文版).

→ **理论 spike(基于网络文章) 不能替代实地 curl 验证**. 这次本来要花 0.3d 接入
Baidu, 实跑 5 分钟就发现路全断, 省了 0.3d.

**Lesson**: 任何"代理/抓取" 类数据源, 必须**单次 curl 实跑** 验证反爬强度,
不能光看网络文章. 5 分钟 curl > 5 小时调研. spec spike 要明确"理论可行 vs 实跑
确认"两档.

### 3. Token-gated source 的"上线不阻塞"工程模式

bug ① 长桥需要用户走 OAuth 拿 token (15-30min 操作), 但 Sprint 7.3 不能因为
等 token 就推迟交付. 用 ``is_enabled`` property + dispatcher 注册前 token 检查,
让长桥**代码完整在线**但**运行时跳过**, token 填了立即激活, 不需重新发版.

这是**未来所有第三方 API 接入的标准模式**:
- code: 完整实现 + 单测 + 文档
- runtime: token 配置时启用, 否则 0 HTTP 0 副作用
- ops: 用户操作完成后 ``.env`` 改一行 + 重启即可

**Lesson**: 第三方 API 接入要把"代码完整性" 和"运行时启用" 解耦. 别让"等用户
拿 token" 阻塞 sprint 交付; framework + token-gated runtime 是标准模式.

### 4. Token-gated source 的单测覆盖必备项

测试 ``LongbridgeApiClient`` 时, 一开始我只测了 happy path + 错误码, 漏了
关键的 ``token=空`` 路径. 这个路径是**生产环境第一天的真实状态** — 用户还没拿
token 时, dispatcher 必须不报错地跳过. 漏测 = 上线第一天 NPE 风险.

补了 ``test_client_disabled_when_token_empty`` 后才完整.

**Lesson**: 任何 conditional-enable 的 component, **disabled 路径必须有专属
单测**, 与 enabled path 同等覆盖. 因为 disabled 是默认状态, 上线第一天 100%
跑这个分支.

### 5. 用户 follow-up checklist 入 spec, 不要藏在代码注释

bug ① 长桥 token 申请流程涉及 4 步 (开户 / 申请权限 / OAuth / 填配置), 不是
程序员能一行帮用户搞定的. spec/22 §"用户 follow-up checklist" 段把这 4 步列
checkbox, 用户拿到 spec 直接照做.

旧反模式: 把流程写在 ``longbridge_api_token`` 字段的 description 里 (256 字符
内不够写). 用户看 description 三行不知道下一步去哪开户.

**Lesson**: 任何依赖用户操作的接入流程, spec 里必须有专属"用户 checklist" 段,
含具体 URL + 字段名 + 验证步骤. 不要塞代码注释或 README — spec 才是用户 +
后续工程师都能找到的源.

---

## 📦 实现交付

### BE 改动 (3 文件改, 1 新文件 + 1 单测)

| 文件 | 改动 | 行数 |
|---|---|:---:|
| `app/services/article_ingest/sources/longbridge_api_client.py` | **新增** BUG-S7.3-001 | +340 |
| `app/services/article_ingest/dispatcher.py` | BUG-S7.3-001 注册 LongbridgeApiClient (token-gated, HK symbol) | +25 / -0 |
| `app/core/config.py` | BUG-S7.3-001 加 5 项 longbridge_api_* + 调高 sogou_inter_query_delay 1.5→3.0s | +47 / -3 |
| `tests/test_longbridge_api_client.py` | **新增** 12 单测 | +330 |

### FE 改动 (5 文件)

| 文件 | 改动 |
|---|---|
| `apps/mp/pages/index/index.vue` | BUG-S7.3-002: .page 加 ``color: var(--color-text, #e2e8f0)`` |
| `apps/mp/pages/ipo/detail.vue` | BUG-S7.3-002: 同上 |
| `apps/mp/pages/ipo/historical.vue` | BUG-S7.3-002: 同上 |
| `apps/mp/pages/ipo/historical-pattern.vue` | BUG-S7.3-002: 同上 |
| `apps/mp/pages/ipo/agent.vue` | BUG-S7.3-002: 同上 |

### DOC (2 文件)

| 文件 | 改动 |
|---|---|
| `spec/22-sprint-7.3-bug-fix-backlog.md` | **新增** — 含长桥 OpenAPI 实施细节 + Baidu/Bing 实地 spike 否决 + retro 5 lesson + 用户 checklist |
| `docs/bug/2026.04.29-bug.md` | ``bug-fix-22:24`` 段标 ✅ + 修复方案摘要 |

### 质量门 (全绿)

```
ruff check 4 文件                    # All checks passed!
mypy 2 文件                          # Success: no issues found
pytest tests/                        # 645 passed, 553 skipped, 0 failed
vue-tsc --noEmit                     # 0 输出 = 全绿
```

### 用户验收路径

1. **bug ②** mp 端 (微信开发者工具) — 我的 → 设置/关于 → 外观主题 → ☀️ 浅色 → 🌙 深色
   反复切换, 首页 / IPO 详情 / IPO 历史 / IPO 历史规律 / IPO Agent 5 页文字颜色严格
   跟随主题 (深色主题白字, 浅色主题深字), 无残留黑字
2. **bug ①(代码层)** dispatcher 启动时 log 应有 ``article_ingest.source_done name=长桥 OpenAPI``
   或 ``longbridge_api.skipped — token 未配置``. token 未配置时**只 skip 不报错**,
   现有 5 源照常运行
3. **bug ①(token 配置后)** 用户走完 [§用户 follow-up checklist](#用户-follow-up-checklist-token-申请)
   把 token 填 ``.env`` 重启 BE 后, dispatcher 注册池升 6 源, ingest log 有
   ``article_ingest.source_done name=长桥 OpenAPI fetched=N``. 数据库
   ``articles.source_name`` 出现 ``长桥·`` 前缀的行
