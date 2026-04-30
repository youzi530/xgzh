# Sprint 8 — `bug-fix-23:31` 拍板项: 多源大V抓取落地 (2026-04-29 23:31–04-30 00:30)

> 状态: ✅ **已交付** Top 4 强推荐组合 (~0.7d, 0 现金成本) — 用户基于 [`spec/23`](./23-multi-channel-kol-spike-matrix.md)
> 28 源决策表拍板 ``Top 4`` 强推荐, 明示**搜狗保留作大V点评来源之一不动**.
>
> 注册池从 5 源升 **7 源**(无 token 时) / **9 源**(token 配后), 大V点评 tab 加二级
> sub-chip ``[全部·大V / 微信公众号 / 长桥社区]`` 分流 KOL 子源.

参考:

- 上游决策: [`spec/23-multi-channel-kol-spike-matrix.md`](./23-multi-channel-kol-spike-matrix.md)
- 用户原始 bug 单 + 拍板: [`docs/bug/2026.04.29-bug.md`](../docs/bug/2026.04.29-bug.md) `bug-fix-23:31` 段
- 现有 KOL tab FE: [`apps/mp/pages/ipo/detail.vue`](../apps/mp/pages/ipo/detail.vue) §`articleFilter / kolSubFilter`
- akshare 实测验证: v1.18.57 实跳, ``stock_info_global_cls`` + ``stock_news_main_cx`` 两接口

---

## 🎯 用户拍板 + 关键约束

```
[✅] Top 4 强推荐: AKShare 财联社电报 + AKShare 金十 + 长桥社区 + FE 二级 chip
[💬] 我希望搜狗作为大V点评的来源之一,这个不要变
```

→ 4 源接入, 搜狗维持原状, FE 大V tab 加 sub-chip 同时呈现"微信"和"长桥社区"两路 KOL.

---

## 🔬 spec/23 推测 vs 实地 spike 调整

spec/23 §C 类推荐基于 AKShare **网络文档**, 实地 spike 发现 v1.18.57 接口名变化 + 真实
能力比预期更优:

| spec/23 推荐 | akshare v1.18.57 实跳结果 | 调整后接入 |
|---|---|---|
| `ak.stock_telegraph_cls` | ❌ AttributeError (旧名已移除) | → `ak.stock_info_global_cls(symbol='全部')` ✅ |
| `ak.js_news` | ❌ AttributeError | → `ak.stock_news_main_cx` ✅ **比金十更优** (财新 = 中国最权威持牌财经媒体之一) |
| `ak.stock_zh_a_alerts_cls` | ❌ AttributeError | → 已被 `stock_info_global_cls` 覆盖 |

**真实可用 + 比预期更优**:

| 接入接口 | 数据源 | 字段 | 量级 |
|---|---|---|---|
| `ak.stock_info_global_cls(symbol)` | 财联社 cls.cn 全球财经 | 标题 / 内容 / 发布日期 / 发布时间 | ~20 条 / 类别, 实时 |
| `ak.stock_news_main_cx()` | 财新网主要新闻 | tag / summary / url | 100 条, 滚动 |

→ **本 sprint spec/23 推荐落地, 实施时根据 akshare v1.18 实测调整 endpoint, 比 spec 推荐
更权威 (财新 vs 金十) + 更稳 (akshare 已封装上游变化, 0 反爬维护)**.

---

## 📦 实现交付

### BE 改动 (3 新文件 + 2 改文件 + 3 单测)

| 文件 | 改动 | 行数 |
|---|---|:---:|
| `app/services/article_ingest/sources/cls_global_client.py` | **新增** S8-001 财联社 (akshare) | +280 |
| `app/services/article_ingest/sources/caixin_client.py` | **新增** S8-002 财新网 (akshare) | +160 |
| `app/services/article_ingest/sources/longbridge_community_client.py` | **新增** S8-003 长桥社区 (与新闻 API 同 token) | +320 |
| `app/services/article_ingest/dispatcher.py` | 注册 3 新源 + 源池表 docstring 升 9 源 | +28 / -10 |
| `app/core/config.py` | 加 ``article_ingest_cls_*`` (2 项) + ``longbridge_community_path`` (1 项) | +35 |
| `tests/test_cls_global_client.py` | **新增** 10 单测 | +250 |
| `tests/test_caixin_client.py` | **新增** 10 单测 | +160 |
| `tests/test_longbridge_community_client.py` | **新增** 11 单测 | +330 |

### FE 改动 (1 文件)

| 文件 | 改动 |
|---|---|
| `apps/mp/pages/ipo/detail.vue` | BUG-S8-FE: ``KOL_PREFIXES = ['微信·', '长桥社区·']`` (扩 KOL 集合) + 大V tab 选中时展开 sub-chip ``[全部·大V / 微信公众号 / 长桥社区]`` + 各 sub 计数 + 智能空状态文案 + 配套 sub-chip CSS (虚边框 + 字号 -2rpx 视觉次级) |

### DOC (2 文件)

| 文件 | 改动 |
|---|---|
| `spec/24-sprint-8-multi-source-kol-rollout.md` | **新增** — 含 spec/23 推荐 vs 实跳调整 + retro 5 lesson |
| `docs/bug/2026.04.29-bug.md` | ``bug-fix-23:31`` 拍板项 ✅ + 修复方案摘要 |

### 注册池升级前后对比

```
Sprint 7.3 (5 源 + 1 token-gated):
  ZhitongRSS / SinaFinance / Xueqiu / EastmoneySearch / SogouWechat
  (+ LongbridgeApi @ token-gated)

Sprint 8 (7 源 + 2 token-gated):
  ZhitongRSS / SinaFinance / **ClsGlobal (新)** / **Caixin (新)**
  / Xueqiu / EastmoneySearch / SogouWechat (用户明示保留)
  (+ LongbridgeApi + **LongbridgeCommunity (新)** @ 同 token-gated)
```

实测 verify:

```python
>>> register_sources(settings, keyword_index)
注册池: 7 源
  - 智通财经
  - 新浪财经
  - 财联社·akshare    ← S8-001 新
  - 财新·akshare      ← S8-002 新
  - 雪球
  - 东方财富搜索
  - 搜狗微信           ← 用户明示保留
longbridge_api_token 配置: False (token 配后 → 9 源含长桥新闻 + 社区)
```

### FE 数据源前缀分流约定

```ts
// ipo/detail.vue 大V tab 分类 (Sprint 8 起)
const WECHAT_PREFIX = '微信·'              // SogouWechat (Sprint 6.9)
const LONGBRIDGE_COMMUNITY_PREFIX = '长桥社区·'  // LongbridgeCommunity (Sprint 8)
const KOL_PREFIXES = [WECHAT_PREFIX, LONGBRIDGE_COMMUNITY_PREFIX]

// 持牌媒体 = source_name 不以 KOL_PREFIXES 任一开头
// 大V点评 = source_name 以 KOL_PREFIXES 之一开头
//   - sub: 微信公众号 = startsWith('微信·')
//   - sub: 长桥社区 = startsWith('长桥社区·')
```

各源命名输出表 (BE 端):

| Client | source_name 前缀 | UX 分类 | 示例 |
|---|---|---|---|
| ZhitongRSSClient | 智通财经 | 持牌媒体 | `智通财经` |
| SinaFinanceClient | 新浪财经 | 持牌媒体 | `新浪财经·港股` |
| EastmoneySearchClient | 东方财富 / `<原 source>` | 持牌媒体 | `东方财富` / `第一财经` |
| XueqiuClient | 雪球 / `<作者>` | 持牌媒体 | `雪球·@用户` |
| **ClsGlobalClient** (S8) | `财联社·` | 持牌媒体 | `财联社·全部` |
| **CaixinClient** (S8) | `财新·` | 持牌媒体 | `财新·宏观` |
| LongbridgeApiClient (S7.3) | `长桥·` | 持牌媒体 | `长桥·新华财经` |
| **SogouWechatClient** | `微信·` | 大V点评 | `微信·每天打个新` (用户明示保留) |
| **LongbridgeCommunityClient** (S8) | `长桥社区·` | 大V点评 | `长桥社区·Cathie Wood` |

### 质量门 (全绿)

```
ruff check 8 文件               ✅ All checks passed
mypy 4 source files             ✅ Success: no issues found
pytest tests/                   ✅ 676 passed (+31 新增), 553 skipped, 0 failed
vue-tsc --noEmit                ✅ 0 errors
ReadLints 5 files               ✅ 0 errors
```

### 用户验收路径

1. **多源 dispatcher** — `cd xgzh/apps/api && uv run python -c "...register_sources..."`
   应输出 7 源 (含财联社·akshare / 财新·akshare). API uvicorn 已自动 reload 加载新代码,
   下次 scheduler ingest 触发时 BE log 应有:
   ```
   article_ingest.source_done name=财联社·akshare fetched=N
   article_ingest.source_done name=财新·akshare fetched=N
   ```
2. **FE 大V tab 二级 chip** — 微信开发者工具 / H5 → 任意 IPO 详情 → 市场文章 tab → 大V点评
   一级 chip → **展开二级 sub-chip** [全部·大V / 微信公众号 / 长桥社区], 切 sub 时
   filteredArticles 立即过滤
3. **搜狗反爬保险** — 实测捕获 `sogou_wechat.antispider_triggered query='乐欣户外'`
   即触发, 但财联社 + 财新即时补位, 大V tab 不会因搜狗反爬而空白
4. **长桥 token follow-up** — 用户拿到 token 填 ``LONGBRIDGE_API_TOKEN``, dispatcher
   自动注册新闻 + 社区两源, 大V tab 长桥社区 sub-chip 立即有数据

---

## 📋 Lessons Learned (Sprint 8 retro)

### 1. spec 推荐基于网络文档, 落地必须**实跳验证**接口名

spec/23 §C 类推荐基于 AKShare 网络文档列出 ``stock_telegraph_cls`` / ``js_news`` /
``stock_zh_a_alerts_cls`` — 这些是**akshare v1.10 旧版接口名**. v1.18.57 实跳全部
``AttributeError``. 用 ``[a for a in dir(ak) if 'cls' in a.lower()]`` 列实际可用接口,
找到 ``stock_info_global_cls`` (旧 telegraph 的合并版) + ``stock_news_main_cx`` (新增的
财新接口, 比金十更权威).

**Lesson**: spec 决策表内的"推荐接入" 必须**附 5 分钟实跳 spike 验证步骤**, 不要只列
"理论可用". 落地实施前先 ``import ak; print(ak.__version__); ak.目标接口(test 参数)``
跑一次, 5 分钟确认接口名 + 字段名, 比写完代码再发现 AttributeError 调整省 0.5d.

### 2. **akshare 同步函数包 `asyncio.to_thread` 不阻塞 event loop**

财联社 / 财新两 client 接入挑战: akshare 是**同步函数**, 直接 await 会阻塞整个 event
loop. 用 ``asyncio.to_thread(runner, symbol=...)`` 让 akshare 在线程池跑, 主 event loop
照常处理其他 source 的 await.

```python
df = await asyncio.to_thread(runner, symbol=symbol)  # 不阻塞 event loop
```

**Lesson**: 接入任何**同步阻塞**第三方库 (requests / akshare / tushare 等), 用
``asyncio.to_thread`` 而非 sync function 直接 await; 这是 fastapi + asyncio 标准模式,
保 dispatcher 多源并发不被单源拖慢.

### 3. **接口无 url 字段时用 hash 占位**保 DB UNIQUE 约束

财联社接口**只有内容 + 时间, 没有 url** (这是 akshare 包装的限制). DB
``articles.original_url UNIQUE`` 约束需要稳定标识, 解法:

```python
def _make_fake_url(content: str, dt: datetime) -> str:
    h = hashlib.sha256(f"{content}|{dt.isoformat()}".encode()).hexdigest()[:16]
    return f"https://cls.cn/detail/{h}"
```

同一条快讯 content + dt 不变 → hash 稳定 → ``ON CONFLICT (original_url) DO NOTHING``
天然幂等. 用户点 url 跳到伪 URL 是 404, 但财联社快讯**正文已随接口全文返回**
(``is_full_text_available=True``), FE 不需要外链跳转.

**Lesson**: 数据源**没 url 字段**时, 用 ``content + time hash`` 占位是 PG ON CONFLICT
幂等的标准模式; 同时 ``is_full_text_available=True`` 让 FE 自己渲染全文不外链, 用户
体验无感.

### 4. token-gated 多源**共享同一 token**, 接入边际成本 0

LongbridgeCommunityClient 复用 ``LongbridgeApiClient`` 的 ``access_token``,
``is_enabled = bool(self._settings.longbridge_api_token)``, 用户填 token 自动同时启用
**新闻 + 社区**两源. 写 community client 时**不要新增 token 配置**, 只加路径配置
``longbridge_community_path``.

**Lesson**: 同一第三方 API 服务下的多个 endpoint, **共享 token 配置, 路径独立配置**.
新增源时不要让用户重新申请 / 输入 token, 边际成本应为 0.

### 5. **二级 sub-chip 的视觉权重必须比一级 chip 低 1 档**

FE 大V tab 加 sub-chip 时, 第一稿用了与一级 chip **相同尺寸+样式**, 视觉混乱用户分不清
"持牌媒体 / 大V点评" 与 "全部·大V / 微信 / 长桥社区" 谁是上下层. 调整后:

| 维度 | 一级 chip | 二级 sub-chip |
|---|---|---|
| 字号 | 24rpx | 22rpx (-2rpx) |
| 边框 | solid | dashed (虚边框, 标识"次级") |
| padding | 8rpx 20rpx | 6rpx 16rpx (-2rpx) |
| 选中态填充 | 0.15 蓝 | 0.08 蓝 (一半亮度) |

**Lesson**: 二级 chip / sub-tab 视觉必须比一级**低 1-2 档** (字号 / 边框样式 / 选中亮度
任一维度), 让用户**毫秒级识别层级关系**. 同尺寸 + 同样式 = 用户认为是平行选项, 视觉灾难.

---

## 🔄 后续 (待用户拍板)

- [ ] 长桥 OpenAPI token 申请 (4 步骤, 见 [spec/22 §用户 follow-up checklist](./22-sprint-7.3-bug-fix-backlog.md))
  → token 填 ``.env`` 后注册池自动 → **9 源**
- [ ] **Sprint 9 拍板按钮** (见 [spec/23 §Sprint 8 拍板按钮](./23-multi-channel-kol-spike-matrix.md)
  剩余项):
  - WeChat Download API SaaS ¥19.9/月 (¥0.7/天彻底解决搜狗反爬, ROI 正向)
  - admin/article_sources/status endpoint (token-gated 灰度监控)
  - 是否继续 spike 微信读书后门 (P3, 合规风险)
