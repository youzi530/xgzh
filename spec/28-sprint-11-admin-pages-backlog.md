# Sprint 11 — `admin-pages` 4 个运营管理页 (2026-05-22 之后, 预计 2-3 天)

> 状态: 📐 **待 code** — 依赖 Sprint 10 RBAC 基础设施完成 (`is_admin` + `get_current_admin`)
>
> Sprint 10 把 RBAC 地基 + 用户管理页做完后, 本 sprint 把剩下 4 个运营管理页一次性
> 全部交付: broker (CRUD + 开户链接) / feedback (列表+删除) / community (帖子管理)
> / knowledge (富文本 CRUD).

参考:

- 上游需求: 用户原始 sprint 单 [`docs/new sprint/2026.0506.md`](../docs/new%20sprint/2026.0506.md) §b/d/e/f
- 上一 sprint: [`spec/27-sprint-10-rbac-backlog.md`](./27-sprint-10-rbac-backlog.md)
- 现有 broker: [`apps/api/app/api/v1/brokers.py`](../apps/api/app/api/v1/brokers.py) (只读)
- 现有 community: [`apps/api/app/api/v1/community.py`](../apps/api/app/api/v1/community.py) (用户视角完整)
- 现有 knowledge: [`apps/api/app/api/v1/knowledge.py`](../apps/api/app/api/v1/knowledge.py) (只读)
- 现有 feedback: [`apps/api/app/api/v1/feedback.py`](../apps/api/app/api/v1/feedback.py) (用户提交)
- 现有 X-Admin-Token 旧路径: [`apps/api/app/api/v1/admin.py`](../apps/api/app/api/v1/admin.py) `list_feedbacks`

---

## 🎯 用户拍板 (3 项 sprint 11 决策)

| # | 决策点 | 选项 | 拍板 |
|---|---|---|:---:|
| Q1 | broker 顶层字段 vs JSONB 处理 | A 仅顶层 / B 仅 JSONB / **C 双字段(顶层 admin 写入 / JSONB 留作 run-time 文案)** | **C** (Sprint 10 拍过) |
| Q2 | community admin 操作粒度 | A 仅删帖 / **B 删帖 + 隐藏(visibility=self_only) + 改 status** / C 完整审核(approve/reject/hidden/delete 4 选项) | **B** ⭐ (新拍板) |
| Q3 | knowledge 内容编辑器 | A 纯 textarea (markdown 源码) / **B 简易富文本(toolbar + textarea 预览)** / C 完整 WYSIWYG | **A** ⭐ (Sprint 11 MVP, 后续 Sprint 12 升级) |

**追加决策 Q2 说明**: 用户 sprint 单原文 "对社区的帖子进行编辑,删除等操作" → "编辑" 不明确 (改作者内容?改状态?). 拍板:
- ✅ 隐藏 (改 visibility=self_only, 帖子还在但其它人看不到)
- ✅ 改 status (published → pending 强制重审)
- ✅ 软删 (status=deleted)
- ❌ 改作者内容 (改了等于篡改用户言论, PIPL 合规风险高, 不做)

**追加决策 Q3 说明**: knowledge 当前只有 3 篇 seed 数据 (spec 目标 30 篇). MVP 阶段 admin 直接写 markdown 源码, 不做富文本编辑器. 后续 Sprint 12 再加 toolbar (粗体/斜体/链接/列表 5 个常用按钮).

---

## 🚀 Sprint 11 任务清单

按 4 个模块拆 4 大块, 每块独立可交付 (拆 commit), 失败一个不影响其它 3 个.

### Module A: broker 管理 (~400 行)

| ID | 任务 | 关键文件 |
|---|---|---|
| BE-S11-A01 | alembic 0018: brokers 加 `open_account_url VARCHAR(500) NULL` | `alembic/versions/0018_brokers_open_account_url.py` |
| BE-S11-A02 | broker_service 加 `create_broker / update_broker / soft_delete_broker` | `app/services/broker_service.py` |
| BE-S11-A03 | broker admin endpoint (POST/PATCH/DELETE) | `app/api/v1/admin_brokers.py` (新增) |
| BE-S11-A04 | `BrokerPublic` schema 加 `open_account_url` 字段 + `/brokers/{slug}/redirect` 优先用顶层 url | `app/schemas/broker.py` + `app/api/v1/brokers.py` |
| BE-S11-A05 | tests/test_admin_brokers.py (5 endpoint × 2 场景) | `tests/` |
| FE-S11-A01 | broker 管理页 (列表/新建/编辑/软删) | `apps/mp/pages/admin/brokers.vue` + `broker-edit.vue` |
| FE-S11-A02 | broker 详情页加 "开户" 按钮 → 直接跳 `open_account_url` | `apps/mp/pages/broker/detail.vue` 微调 |
| FE-S11-A03 | me 页 admin section 加 "券商管理" entry | `apps/mp/pages/me/index.vue` |

### Module B: feedback 管理 (~250 行)

| ID | 任务 | 关键文件 |
|---|---|---|
| BE-S11-B01 | feedback_service 加 `update_feedback / soft_delete_feedback` (现状无软删, 需要先加 `deleted_at`) | `app/services/feedback_service.py` + alembic 0019 |
| BE-S11-B02 | feedback admin endpoint (列表/详情/PATCH 改 status / DELETE 软删) | `app/api/v1/admin_feedbacks.py` (新增) |
| BE-S11-B03 | 老 `GET /admin/feedbacks` (X-Admin-Token 路径) 保留, 不动. Q4 双系统并存 | — |
| BE-S11-B04 | tests/test_admin_feedbacks.py | `tests/` |
| FE-S11-B01 | feedback 管理页 (列表/详情/状态切换/软删) | `apps/mp/pages/admin/feedbacks.vue` + `feedback-detail.vue` |
| FE-S11-B02 | me 页 admin section 加 "反馈管理" entry | 微调 |

### Module C: community 管理 (~350 行)

| ID | 任务 | 关键文件 |
|---|---|---|
| BE-S11-C01 | community_post_service 加 `admin_list_posts / admin_update_post_status / admin_hide_post / admin_delete_post` | `app/services/community/post_service.py` |
| BE-S11-C02 | community admin endpoint (列表+filter / PATCH status / PATCH visibility / DELETE) | `app/api/v1/admin_community.py` (新增) |
| BE-S11-C03 | 现有 spec/13 设计的 `/admin/community/queue` 4 选项审核 (approve/reject/hidden_continue/delete) — 简化为 PATCH 直接改 status, 不实现完整队列 (Q2 决策 B) | — |
| BE-S11-C04 | tests/test_admin_community.py | `tests/` |
| FE-S11-C01 | community 管理页 (列表 + filter by status / visibility / category) | `apps/mp/pages/admin/community.vue` + `community-post-detail.vue` |
| FE-S11-C02 | me 页 admin section 加 "社区管理" entry | 微调 |

### Module D: knowledge 管理 (~400 行)

| ID | 任务 | 关键文件 |
|---|---|---|
| BE-S11-D01 | knowledge_service 加 `create_article / update_article / delete_article / toggle_publish` | `app/services/knowledge_service.py` |
| BE-S11-D02 | knowledge admin endpoint (POST/PATCH/DELETE + 发布开关) | `app/api/v1/admin_knowledge.py` (新增) |
| BE-S11-D03 | `KnowledgeArticleAdmin` schema (含 content_md / source / legal_disclaimer 等 admin 编辑字段) | `app/schemas/knowledge.py` |
| BE-S11-D04 | tests/test_admin_knowledge.py | `tests/` |
| FE-S11-D01 | knowledge 管理页 (列表 + filter by category/level/published) | `apps/mp/pages/admin/knowledge.vue` |
| FE-S11-D02 | knowledge 编辑页 (textarea markdown + 分类/级别 select + 发布开关) | `apps/mp/pages/admin/knowledge-edit.vue` |
| FE-S11-D03 | me 页 admin section 加 "知识管理" entry | 微调 |

### Module E: 共享 (~150 行)

| ID | 任务 | 关键文件 |
|---|---|---|
| BE-S11-E01 | admin_audit_logs 表 (Sprint 10 留的尾巴) — alembic 0020 + service | `alembic/versions/0020_admin_audit_logs.py` + `app/services/admin_audit_service.py` |
| BE-S11-E02 | 所有 admin write endpoint 包一层 audit log decorator | 各 admin_*.py |
| FE-S11-E01 | 共用 admin 组件 (页头返回按钮 / 二次确认 modal / 列表分页 hook) | `apps/mp/components/admin/*` |

---

## 🔬 各 module 详细设计

### Module A — broker 管理

#### BE-S11-A01 — alembic 0018

```python
# apps/api/alembic/versions/0018_brokers_open_account_url.py
def upgrade() -> None:
    op.add_column(
        "brokers",
        sa.Column("open_account_url", sa.String(500), nullable=True),
    )
    # 历史数据: 如果 promotion.referral_url 有值, 拷到顶层 (admin 之后能改顶层)
    op.execute(
        sa.text(
            """
            UPDATE brokers
            SET open_account_url = promotion->>'referral_url'
            WHERE promotion ? 'referral_url' AND promotion->>'referral_url' IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_column("brokers", "open_account_url")
```

**关键**:
- `promotion.referral_url` JSONB 不删 — 兼容现有 `/brokers/{slug}/redirect` 跳转和现有 seed 数据
- 顶层 `open_account_url` 是 admin 编辑入口, redirect 优先用顶层, fallback 走 JSONB:

```python
# apps/api/app/api/v1/brokers.py /brokers/{slug}/redirect 改造
target_url = (
    broker.open_account_url  # 优先用顶层 (admin 维护)
    or (broker.promotion or {}).get("referral_url")  # fallback JSONB
)
if not target_url:
    raise HTTPException(404, detail={"code": "no_referral_url"})
```

#### BE-S11-A03 — broker admin endpoint

```python
# apps/api/app/api/v1/admin_brokers.py
router = APIRouter(prefix="/admin/brokers", tags=["admin"])

@router.post("", response_model=BrokerAdminDetail, status_code=201)
async def create_broker(
    body: BrokerCreate,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
): ...

@router.patch("/{slug}", response_model=BrokerAdminDetail)
async def update_broker(
    slug: str,
    body: BrokerUpdate,  # 含 open_account_url / name_zh / name_en / promotion (JSONB merge) 等
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
): ...

@router.delete("/{slug}", status_code=204)
async def delete_broker(  # 软删 (deleted_at = NOW())
    slug: str,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
): ...
```

**关键 schema** (`app/schemas/broker.py` 加):

```python
class BrokerCreate(BaseModel):
    slug: str = Field(..., pattern=r"^[a-z0-9-]+$", min_length=2, max_length=50)
    name_zh: str = Field(..., min_length=1, max_length=50)
    name_en: str | None = None
    logo_url: str | None = None
    market_support: list[str] = []
    licenses: list[dict] = []
    open_account_url: str | None = None
    display_order: int = 999
    is_active: bool = True


class BrokerUpdate(BaseModel):
    """全字段可选 PATCH; JSONB 字段 (promotion / fees / features) 走 merge 不是覆盖."""
    name_zh: str | None = None
    name_en: str | None = None
    logo_url: str | None = None
    open_account_url: str | None = None
    display_order: int | None = None
    is_active: bool | None = None
    # JSONB 走单独 endpoint 或显式 merge field
    promotion_patch: dict | None = None  # admin 改促销文案; service 内部 jsonb merge
```

#### FE-S11-A01 — broker 管理页

```
pages/admin/brokers.vue (列表)
├── 顶部: "新建券商" 按钮 + 搜索框 (slug/name_zh 模糊)
├── 列表 item: logo + name_zh + 上下架 chip + display_order
└── tap → broker-edit.vue?slug=xxx (或 ?new=1 新建)

pages/admin/broker-edit.vue (新建 / 编辑)
├── 表单: slug (新建时可改, 编辑时只读) / name_zh / name_en / logo_url
├── 开户链接: open_account_url (text input + "测试跳转" 按钮)
├── 上下架: switch is_active
├── 排序: number input display_order
├── 促销文案 (折叠面板): title / description / end_at
└── 底部: "保存" / "删除" (红色)
```

---

### Module B — feedback 管理

#### BE-S11-B01 — alembic 0019 + service

```python
# apps/api/alembic/versions/0019_feedbacks_soft_delete.py
def upgrade() -> None:
    op.add_column("feedbacks", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("feedbacks", sa.Column("admin_status", sa.String(20), nullable=True))  # pending/reviewed/resolved/closed
    op.add_column("feedbacks", sa.Column("admin_note", sa.Text, nullable=True))  # admin 内部备注
    op.add_column("feedbacks", sa.Column("reviewed_by", sa.UUID, sa.ForeignKey("users.user_id"), nullable=True))
    op.add_column("feedbacks", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
```

```python
# apps/api/app/services/feedback_service.py 加
async def update_feedback(
    session: AsyncSession,
    *,
    feedback_id: UUID,
    admin: User,
    admin_status: str | None = None,
    admin_note: str | None = None,
) -> Feedback:
    """admin 改 feedback 处理状态 + 内部备注. 不能改 content (用户原文不可篡改)."""
    fb = await session.get(Feedback, feedback_id)
    if not fb or fb.deleted_at:
        raise FeedbackNotFoundError(...)
    if admin_status:
        fb.admin_status = admin_status
        fb.reviewed_by = admin.user_id
        fb.reviewed_at = datetime.now(UTC)
    if admin_note is not None:
        fb.admin_note = admin_note
    await session.commit()
    return fb


async def soft_delete_feedback(...): ...  # 标 deleted_at
```

#### BE-S11-B02 — feedback admin endpoint

```python
# apps/api/app/api/v1/admin_feedbacks.py
@router.get("", response_model=AdminFeedbackList)
async def list_admin_feedbacks(
    q: str | None = None,
    category: str | None = None,
    admin_status: str | None = None,  # 新字段过滤
    is_deleted: bool = False,
    page: int = 1,
    page_size: int = 20,
    admin: User = Depends(get_current_admin), ...
): ...
# (vs 老 /admin/feedbacks X-Admin-Token 路径: 保留, 两者**并存**, ops 通道仍可用 token)

@router.get("/{feedback_id}", response_model=AdminFeedbackDetail)
async def get_admin_feedback(...): ...

@router.patch("/{feedback_id}", response_model=AdminFeedbackDetail)
async def update_admin_feedback(
    feedback_id: UUID,
    body: AdminFeedbackUpdate,  # { admin_status, admin_note }
    admin: User = Depends(get_current_admin), ...
): ...

@router.delete("/{feedback_id}", status_code=204)
async def delete_admin_feedback(...): ...
```

---

### Module C — community 管理

按 Q2 决策 B (简化 — 删/隐藏/改 status, 不做完整审核队列):

```python
# apps/api/app/api/v1/admin_community.py
@router.get("/posts", response_model=AdminPostList)
async def list_admin_posts(
    q: str | None = None,
    status: str | None = None,  # pending/published/rejected/deleted/hidden
    visibility: str | None = None,
    has_reports: bool | None = None,  # 只看被举报的
    page: int = 1, page_size: int = 20,
    admin: User = Depends(get_current_admin), ...
): ...

@router.patch("/posts/{post_id}/status", response_model=AdminPostDetail)
async def admin_update_post_status(
    post_id: UUID,
    body: PostStatusUpdate,  # { status: Literal["pending","published","rejected","deleted","hidden"], reason: str | None }
    admin: User = Depends(get_current_admin), ...
):
    """admin 强制改帖子 status. status='deleted' 等同于软删, 但保留 audit trail."""

@router.patch("/posts/{post_id}/visibility", response_model=AdminPostDetail)
async def admin_update_post_visibility(
    post_id: UUID,
    body: VisibilityUpdate,  # { visibility: Literal["public","self_only"] }
    admin: User = Depends(get_current_admin), ...
):
    """快捷隐藏: 不动 status, 只把 visibility 改 self_only (作者还能看, 其它人看不见)."""

@router.delete("/posts/{post_id}", status_code=204)
async def admin_delete_post(...): ...  # 等同于 PATCH status=deleted
```

**关键设计**:
- **不改作者内容**: 不暴露 `content` patch endpoint, 防 PIPL 篡改用户言论风险
- **简化 vs spec/13 4 选项审核**: 当前 community 流量小, MVP 不实现"审核队列", 直接 PATCH status. 后续 Sprint 12 再加完整队列 + SLA
- **被举报快捷过滤**: `?has_reports=true` 让 admin 一眼看到需要处理的

---

### Module D — knowledge 管理

```python
# apps/api/app/api/v1/admin_knowledge.py
@router.get("", response_model=AdminKnowledgeList)
async def list_admin_articles(
    q: str | None = None,  # slug/title 模糊
    category: str | None = None,
    level: int | None = None,
    is_published: bool | None = None,
    page: int = 1, page_size: int = 20,
    admin: User = Depends(get_current_admin), ...
): ...

@router.post("", response_model=KnowledgeArticleAdmin, status_code=201)
async def create_article(body: KnowledgeArticleCreate, ...): ...

@router.patch("/{slug}", response_model=KnowledgeArticleAdmin)
async def update_article(slug: str, body: KnowledgeArticleUpdate, ...): ...

@router.delete("/{slug}", status_code=204)
async def delete_article(slug: str, ...): ...

@router.patch("/{slug}/publish", response_model=KnowledgeArticleAdmin)
async def toggle_publish(slug: str, body: TogglePublishRequest, ...):
    """快速发布/下架开关 (独立 endpoint, 不用 PATCH 全 body)."""
```

**关键 schema**:

```python
class KnowledgeArticleCreate(BaseModel):
    slug: str = Field(..., pattern=r"^[a-z0-9-]+$", min_length=3, max_length=60)
    title: str = Field(..., min_length=2, max_length=100)
    category: Literal["hk", "cn", "general"]
    level: int = Field(1, ge=1, le=3)
    tags: list[str] = []
    content_md: str = Field(..., min_length=100)  # 至少 100 字, 防 admin 发空文章
    source: str | None = None
    source_url: str | None = None
    legal_disclaimer: str | None = None
    is_published: bool = False  # 默认草稿, 写完再发布
```

#### FE-S11-D02 — knowledge 编辑页

按 Q3 决策 A (纯 textarea markdown 源码):
- 顶部 form: slug / title / category select / level select / is_published switch
- 中间 textarea: `content_md` (高度自适应 60vh, 等宽字体)
- 底部 "预览" 按钮: 跳 knowledge/detail?slug=draft-{slug} 临时预览 (post-MVP)
- 底部 "保存" / "保存并发布" / "删除" 3 按钮

---

### Module E — admin audit logs (Sprint 10 留的尾巴)

#### BE-S11-E01 — alembic 0020 + service

```python
# apps/api/alembic/versions/0020_admin_audit_logs.py
def upgrade() -> None:
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("admin_id", sa.UUID, sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),  # "user.grant_vip" / "broker.update" / "community.hide_post"
        sa.Column("target_type", sa.String(30), nullable=False),  # "user" / "broker" / "post" / "feedback" / "article"
        sa.Column("target_id", sa.String(50), nullable=False),  # UUID 或 slug
        sa.Column("changes", sa.JSON, nullable=True),  # diff: { before: {...}, after: {...} }
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_audit_admin_action_time", "admin_audit_logs", ["admin_id", "action", "created_at"])
    op.create_index("ix_audit_target", "admin_audit_logs", ["target_type", "target_id"])
```

```python
# apps/api/app/services/admin_audit_service.py
async def log_admin_action(
    session: AsyncSession,
    admin: User,
    action: str,  # "user.grant_vip"
    target_type: str,  # "user"
    target_id: str,
    changes: dict | None = None,
    request: Request | None = None,
) -> None:
    """async fire-and-forget, 失败不抛 (log 失败不该阻塞主操作)."""
    try:
        log = AdminAuditLog(
            admin_id=admin.user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            changes=changes,
            ip=request.client.host if request else None,
            user_agent=request.headers.get("user-agent")[:500] if request else None,
        )
        session.add(log)
        await session.commit()
    except Exception as e:
        logger.warning(f"admin_audit.log_failed action={action}: {e}")
```

**用法** (Sprint 11 各 admin endpoint 收尾时调用):
```python
@router.post("/{user_id}/grant-vip")
async def grant_vip(...):
    # ... 业务逻辑 ...
    await admin_audit_service.log_admin_action(
        session, admin, "user.grant_vip", "user", str(user_id),
        changes={"days": body.days, "reason": body.reason},
        request=request,
    )
    return result
```

---

## ✅ 验收标准

| # | 验证场景 | 验证方法 |
|---|---|---|
| 1 | admin 新建 broker, FE 列表立即出现 | curl + 真机 |
| 2 | admin 编辑 broker 的 open_account_url, `GET /brokers/{slug}/redirect` 301 到新 url | curl |
| 3 | admin 软删 broker, `GET /brokers` 列表不再出现, DB `deleted_at IS NOT NULL` | curl + 查 DB |
| 4 | admin 改 feedback admin_status 为 "resolved", 列表 filter `?admin_status=resolved` 命中 | curl |
| 5 | admin 隐藏帖子(visibility=self_only), 其它用户列表不再看到, 作者自己仍能看 | 真机 (admin + 普通用户两账号) |
| 6 | admin 把帖子 status 改 "deleted", 全部用户看不到 | 真机 |
| 7 | admin 新建 knowledge 文章 (is_published=false), 普通用户列表不出现; admin 改 published=true 后立即出现 | 真机 |
| 8 | admin 删 knowledge 文章, 普通用户列表不再出现 | 真机 |
| 9 | 每一次 admin 写操作都进 `admin_audit_logs` 表 (用 `SELECT * FROM admin_audit_logs ORDER BY created_at DESC LIMIT 10` 验证) | 查 DB |
| 10 | 普通用户调任意 `/admin/*` endpoint 全部 403 | curl |
| 11 | tests/test_admin_brokers.py / feedbacks / community / knowledge 全过 | CI |

---

## 🛡️ 风险 + 回滚

| 风险 | 概率 | 应对 |
|---|:---:|---|
| broker.open_account_url 迁移把 referral_url 拷错 | 中 | UP migration 在 staging 跑一遍验证, DOWN 不会丢数据 (顶层删了, JSONB 还在) |
| feedback 加 deleted_at 列时锁表 | 低 | 表行数预期 < 1000, PG add column with NULL default 是 instant |
| admin 误删生产 broker 影响真实用户 | **高** | FE 双 modal: 1) "确认删除 富途证券?" 2) "此操作不可撤销, 30 天内可联系 ops 恢复, 输入 "删除" 确认". BE 软删可由 DB 直接 UPDATE 恢复 |
| admin 大量隐藏帖子被申诉 | 中 | audit log + 隐藏需填 reason. 用户可走"申诉"流程 (Sprint 12 加) |
| knowledge 富文本 (textarea md) 用户体验差 | 中 | MVP 接受, 用户拍板 Q3 = A; Sprint 12 升级 toolbar |
| admin_audit_logs 表写爆 (admin 每秒操作 1 次) | 低 | 表分区 / TTL 清理后置 — Sprint 13+; MVP 期暂不考虑 |

**回滚预案**:
1. 4 个新 admin 路由按 module 独立 commit, 任一 module 出问题 `git revert` 单个 commit 即可
2. alembic downgrade 顺序: 0020 → 0019 → 0018; 每步独立可回滚
3. FE 4 个 admin 页路由独立, 出问题在 me 页隐藏对应 entry 即可

---

## 📦 上线 checklist

- [ ] `alembic upgrade head` 跑 0018 + 0019 + 0020 三个 migration
- [ ] 验证 brokers 表 `open_account_url` 列从 promotion.referral_url 拷贝成功
- [ ] BE 测试全过
- [ ] 真机 admin 跑通 4 个管理页 (新建 broker / 处理 feedback / 隐藏帖子 / 写一篇 knowledge)
- [ ] 真机普通用户验证 me 页**没有** 4 个新 admin entry, 调 endpoint 全 403
- [ ] 查 `admin_audit_logs` 表, 上面 4 个操作都有记录

---

## 🔮 后续 sprint (Sprint 12 占位)

- knowledge 富文本编辑器 (toolbar: 粗体/斜体/链接/列表/图片上传)
- community 完整审核队列 (4 选项 approve/reject/hidden_continue/delete + 24h SLA + 钉钉告警)
- admin_audit_logs 查询 UI (按 admin / action / 时间过滤)
- broker promotion JSONB 富表单 (当前 admin 改 promotion 还是要写 JSON, 不太友好)
- 用户申诉流程 (被隐藏/封禁的用户可申诉)

---

## 📐 设计哲学

1. **4 个模块独立可拆 commit** — 任一模块出问题不阻塞其它 3 个发布. AGENTS R4 要求拆分, 这里天然支持
2. **admin 不能改用户原文** — community.content / feedback.content 没有 admin patch endpoint, 防 PIPL 篡改风险
3. **admin_audit_logs fire-and-forget** — log 失败不阻塞业务. 监控通过单独 alert 跟 (log error 频率)
4. **双 modal 高破坏性操作** — 删除 broker / 软删用户 等不可逆操作必须 2 道 modal 确认, 防误操作 
5. **隐藏 vs 删除分开** — community 给 admin 提供"温和"操作(隐藏)和"强硬"操作(删除), 大多数情况隐藏即可, 保留申诉空间
