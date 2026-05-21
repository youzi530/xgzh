# Sprint 10 — `rbac-foundation` RBAC 基础 + 用户管理页 (2026-05-21 起, 预计 1-2 天)

> 状态: ✅ **已完成** — 8 任务全 done, 29 新增 tests + 1276 全量回归 + curl 端到端 smoke 全过
>
> 用户在 Sprint 9 之后(实测了 5 项 bug 修复)继续提出 RBAC 需求: 13007458553
> 为管理员, 能在小程序内管理用户(列表/搜索/编辑/删 + 加 VIP 时长), 后续 sprint
> 加 broker / feedback / community / knowledge 4 个管理页.
>
> 本 sprint 聚焦 **RBAC 基础设施 + 用户管理页**, 4 个运营管理页拆到 Sprint 11.

参考:

- 上游需求: 用户原始 sprint 单 [`docs/new sprint/2026.0506.md`](../docs/new%20sprint/2026.0506.md)
- 上一 sprint: [`spec/26-sprint-9-bug-fix-backlog.md`](./26-sprint-9-bug-fix-backlog.md)
- 现有鉴权: [`apps/api/app/security/deps.py`](../apps/api/app/security/deps.py) (JWT) + [`apps/api/app/security/admin.py`](../apps/api/app/security/admin.py) (X-Admin-Token)
- 现有 me 页: [`apps/mp/pages/me/index.vue`](../apps/mp/pages/me/index.vue)
- 现有 User ORM: [`apps/api/app/db/models/user.py`](../apps/api/app/db/models/user.py)
- 现有 VIP 加时 service: [`apps/api/app/services/vip_service.py`](../apps/api/app/services/vip_service.py) `extend_membership()`
- AGENTS R5: 禁止 `.env` 进 git, 禁止 `DROP TABLE`; 管理员手机号写 migration 不写 settings

---

## 🎯 用户拍板 (4 项关键决策)

| # | 决策点 | 选项 | 拍板 |
|---|---|---|:---:|
| Q1 | 管理员识别机制 | A `settings.admin_phones` 白名单(硬编码) / **B `users.is_admin BOOLEAN` 列 + migration 初始化** / C `users.role` enum + RBAC 矩阵 | **B** ⭐ |
| Q2 | broker 开户链接字段 | A 复用 `promotion.referral_url` JSONB / B 仅顶层 `open_account_url` / **C 双字段并存(顶层 admin 写入 + JSONB 留作促销文案)** | **C** ⭐ (Sprint 11 实施) |
| Q3 | sprint 拆分 | **A 拆 2 sprint** (Sprint 10 RBAC+用户管理 / Sprint 11 4 运营页) / B 单 sprint 精简 / C 一口气全做 | **A** ⭐ |
| Q4 | X-Admin-Token 老路径 | **A 双系统并存**(ops 通道 + JWT in-app) / B 全部迁移到 JWT / C 按路径拆 ops vs business | **A** ⭐ |

→ 长期方向: 未来加 super_admin / content_editor 等多 role 时, 再拆 `users.role enum`; 当前 boolean 足够 2 层(普通用户 / 管理员).
→ X-Admin-Token 保留作 ops 通道 (curl/Postman/CI 脚本可用, 不依赖用户登录), 新增 JWT admin 走 in-app 管理页. 两者权限**重叠**不冲突.
→ 13007458553 在 alembic 0017 migration 里**写死**为 `is_admin=true`. 不进 `settings`, 防止环境变量被改丢失管理员.

---

## 🚀 Sprint 10 任务清单

| ID | 任务 | 影响 | 关键文件 |
|---|---|:---:|---|
| BE-S10-001 | alembic 0017: `users` 加 `is_admin BOOLEAN DEFAULT false NOT NULL`, 初始化 13007458553 | DB | `alembic/versions/0017_users_is_admin.py` |
| BE-S10-002 | `require_admin` JWT 依赖 | Security | `app/security/deps.py` 新增 `get_current_admin` |
| BE-S10-003 | `UserPublic.is_admin` 派生字段 + `GET /me` 下发 | API | `app/schemas/auth.py` + `app/api/v1/me.py` |
| BE-S10-004 | 用户管理 5 endpoint (列表/搜索/详情/更新/删除) | API | `app/api/v1/admin_users.py` (新增) |
| BE-S10-005 | VIP 加时 admin endpoint | API | `app/api/v1/admin_users.py` 同上 |
| BE-S10-006 | tests: `test_admin_rbac.py` + `test_admin_users.py` | QA | `tests/` (新增) |
| FE-S10-001 | `authStore.isAdmin` 暴露 + 我的页 admin section | FE | `apps/mp/stores/auth.ts` + `apps/mp/pages/me/index.vue` |
| FE-S10-002 | 用户管理页 (列表 / 搜索 / 详情 / 改禁用 / 加 VIP 时长 / 软删) | FE | `apps/mp/pages/admin/users.vue` + `users-detail.vue` (新增) |

---

## 🔬 详细设计

### BE-S10-001 — alembic 0017 migration

```python
# apps/api/alembic/versions/0017_users_is_admin.py
"""users.is_admin column + init 13007458553 as admin (Sprint 10 BE-S10-001)."""

from alembic import op
import sqlalchemy as sa

revision = "0017_users_is_admin"
down_revision = "0016_users_password_email"
branch_labels = None
depends_on = None

# 初始管理员手机号 (用户拍板, Sprint 10 Q1)
INITIAL_ADMIN_PHONE = "13007458553"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index(
        "ix_users_is_admin",
        "users",
        ["is_admin"],
        postgresql_where=sa.text("is_admin = true"),
    )
    # 若 13007458553 已存在, 标为 admin; 不存在则什么都不做 (用户首次登录注册时会自动建)
    # 注: 这里不主动插入 user 行, 避免没注册过的手机号被建空用户
    op.execute(
        sa.text(
            "UPDATE users SET is_admin = true WHERE phone = :phone"
        ).bindparams(phone=INITIAL_ADMIN_PHONE)
    )


def downgrade() -> None:
    op.drop_index("ix_users_is_admin", table_name="users")
    op.drop_column("users", "is_admin")
```

**关键设计**:
- `server_default="false"`: 老用户 migration 后默认非 admin, 不破坏既有行为
- `is_admin=true` 部分索引: 全表 admin 总数极少 (期望 < 10), 部分索引节省空间
- migration 不主动 INSERT user — 让 13007458553 走正常注册流程, migration 只 UPDATE; 若该手机还没注册过, 注册时通过 `app_event` 自动检测白名单(见 BE-S10-002 注释)

**追加保护机制** (`app/services/auth_service.py`):

```python
# 注册或首次登录时, 检查是否在初始 admin 白名单, 是则自动设 is_admin=true
INITIAL_ADMIN_PHONES = frozenset(["13007458553"])  # 与 migration 同步, 改时记得改两处

async def _maybe_grant_initial_admin(user: User) -> None:
    """Sprint 10 BE-S10-001: 13007458553 首次注册时自动 is_admin=true.
    
    migration 0017 只能 UPDATE 已存在的行, 未注册的手机号要靠这里兜底.
    """
    if user.phone in INITIAL_ADMIN_PHONES and not user.is_admin:
        user.is_admin = True
        logger.info(f"admin.initial_grant user_id={user.user_id} phone={user.phone}")
```

调用点: `verify_phone_login` 创建新用户后 + `register_with_password` 创建新用户后(2 处).

---

### BE-S10-002 — `require_admin` JWT 依赖

```python
# apps/api/app/security/deps.py 新增
async def get_current_admin(
    user: User = Depends(get_current_user),
) -> User:
    """JWT 鉴权 + is_admin=true 检查. 用于 in-app 管理页 endpoint.
    
    与 require_admin_token (X-Admin-Token) 是两套并存机制:
    - get_current_admin: 用户走小程序登录后调用, RBAC 走 users.is_admin
    - require_admin_token: 走 ops shell 脚本, 用环境变量 OPS_ADMIN_TOKEN
    
    Sprint 10 BE-S10-002.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "not_admin",
                "message": "需要管理员权限",
            },
        )
    return user
```

**关键决策**:
- 复用 `get_current_user` 链路(JWT + 黑名单 + status==1), 不重复实现
- 403 不 404 — 401 / 403 / 404 用法各有歧义, 这里明确"已认证但权限不够"用 403
- 错误码 `not_admin`, 前端可针对性 toast(避免泛用 "无权限" 误导排查)

---

### BE-S10-003 — `UserPublic.is_admin` 派生字段

```python
# apps/api/app/schemas/auth.py UserPublic 加字段
class UserPublic(BaseModel):
    # ... 现有字段 ...
    is_admin: bool = False  # Sprint 10 BE-S10-003: 由 ORM .is_admin 派生
    
    @classmethod
    def from_orm(cls, user: User) -> "UserPublic":
        # _derive_has_flags 已有, 在 from_orm 里把 is_admin 也拷过来
        public = cls.model_construct(
            # ... 现有字段拷贝 ...
            is_admin=user.is_admin,
        )
        return cls._derive_has_flags(public, user)
```

**FE 用法** (Sprint 10 FE-S10-001):
```ts
// stores/auth.ts
const isAdmin = computed(() => state.value.user?.is_admin === true)
```

不下发"权限矩阵", 因为 v1 只有 1 个 bool 不需要; 后续 Sprint 11 加 4 个管理页时仍走同一个 is_admin = true 判定.

---

### BE-S10-004 — 用户管理 5 endpoint

新文件 `apps/api/app/api/v1/admin_users.py`:

```python
"""Admin 用户管理 endpoint (Sprint 10 BE-S10-004).

走 JWT + is_admin=true 鉴权 (require_admin), 不是 X-Admin-Token. 
用于小程序管理员页面调用.
"""

router = APIRouter(prefix="/admin/users", tags=["admin"])

# 1. 列表 + 搜索 (手机 / 昵称 / 邮箱模糊匹配)
@router.get("", response_model=AdminUserListResponse)
async def list_users(
    q: str | None = Query(None, description="搜索关键词: 手机号/昵称/邮箱模糊匹配"),
    is_admin: bool | None = None,  # 过滤管理员
    is_deleted: bool = False,  # 是否含软删用户
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserListResponse: ...

# 2. 单个用户详情 (含 VIP / 邀请 / 软删历史)
@router.get("/{user_id}", response_model=AdminUserDetail)
async def get_user(
    user_id: UUID,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
): ...

# 3. 编辑用户 (status / nickname / region)
@router.patch("/{user_id}", response_model=AdminUserDetail)
async def update_user(
    user_id: UUID,
    body: AdminUserUpdate,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
): ...

# 4. 软删用户 (走 user_deletion_service 现有 PIPL §47 路径)
@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: UUID,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
): ...

# 5. 加 VIP 时长 (BE-S10-005)
@router.post("/{user_id}/grant-vip", response_model=AdminUserDetail)
async def grant_vip(
    user_id: UUID,
    body: GrantVipRequest,  # { days: int, reason: str }
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
): ...
```

**关键 schema**:

```python
class AdminUserListItem(BaseModel):
    user_id: UUID
    phone: str | None
    email: str | None
    nickname: str | None
    is_admin: bool
    status: int  # 1=active, 0=disabled, -1=banned
    deleted_at: datetime | None
    vip_end_at: datetime | None  # 从 vip_memberships 查
    invite_count: int  # 从 invite_rewards 聚合
    created_at: datetime


class AdminUserUpdate(BaseModel):
    """Admin 能改的字段; 受限 (不能直接 patch is_admin 防止越权)."""
    nickname: str | None = Field(None, min_length=1, max_length=20)
    region: str | None = Field(None, max_length=20)
    status: Literal[1, 0, -1] | None = None  # active / disabled / banned
    # is_admin / phone / email 这些不让 patch — 安全 / PIPL 上要走单独流程


class GrantVipRequest(BaseModel):
    days: int = Field(..., ge=1, le=365)  # 防 admin 误操作 999999 天
    reason: str = Field(..., min_length=2, max_length=200)  # 必填理由, 写 audit log
```

**幂等 / 审计**:
- delete_user 不实际删行 (PIPL §47 30 天硬删走 cron), 只标 `deleted_at`. 与现有 `user_deletion_service.soft_delete_user` 复用.
- grant_vip **不**幂等(用户拍板) — 同一 admin 连续点 2 次 = 加 2N 天. UI 提示"已加 N 天, 是否继续?"由 FE 处理.
- 所有 admin 操作记 audit log: 复用现有 `user_deletions` 表? 不, 新建 `admin_audit_logs` 更通用. 但 Sprint 10 不实现, 等 Sprint 11 一起加 (P1 风险点).

---

### BE-S10-005 — `grant_vip` 实现细节

```python
@router.post("/{user_id}/grant-vip")
async def grant_vip(...):
    target = await user_service.get_user_by_id(session, user_id)
    if not target or target.deleted_at:
        raise HTTPException(404, detail={"code": "user_not_found", ...})
    
    # 复用 vip_service.extend_membership (现有, invite 奖励也走这个)
    await vip_service.extend_membership(
        session, user=target, days=body.days, source="admin_grant",
        source_meta={"admin_id": str(admin.user_id), "reason": body.reason},
    )
    await session.commit()
    
    logger.info(
        f"admin.grant_vip admin_id={admin.user_id} target_id={user_id} "
        f"days={body.days} reason={body.reason[:50]}"
    )
    return await _build_admin_user_detail(session, target)
```

**安全**:
- `body.days ≤ 365`: 单次最多 365 天 (1 年). 想加更多 = 多次操作, 强制 audit log
- `body.reason` 必填: 不允许默认空 reason, 防止滥用
- `source="admin_grant"`: vip_memberships 表已有 source 字段, 后续 audit 能区分

---

### FE-S10-001 — `authStore.isAdmin` + 我的页 admin section

```ts
// apps/mp/stores/auth.ts 新增 computed
export const useAuthStore = defineStore('auth', () => {
  // ... 现有 ...
  const isAdmin = computed(() => state.value.user?.is_admin === true)
  return { ..., isAdmin }
})
```

```vue
<!-- apps/mp/pages/me/index.vue, 在 entry-list (券商/反馈) 之后, "绑定邀请人" 之前插入 -->
<view v-if="isAdmin" class="admin-section">
  <view class="section-header">
    <text class="section-title">管理员</text>
    <text class="section-subtitle">仅管理员可见</text>
  </view>
  <view class="entry-list">
    <view class="entry-item" @tap="gotoAdmin('users')">
      <text class="entry-icon">👥</text>
      <text class="entry-title">用户管理</text>
      <text class="entry-arrow">›</text>
    </view>
    <!-- Sprint 11 再加 broker / feedback / community / knowledge 4 个 -->
  </view>
</view>
```

**关键决策**:
- Sprint 10 只显示"用户管理" 1 个入口. Sprint 11 来加其它 4 个时, 同一个 admin-section 内追加 4 个 entry-item 即可, 不重写
- "仅管理员可见" 4 字明示, 避免管理员困惑"为啥我有这块别人没有"
- 用 emoji 不用图片: MVP 快速; 后续 Sprint 12+ 可统一换 icon font

---

### FE-S10-002 — 用户管理页

新文件 `apps/mp/pages/admin/users.vue` (列表 + 搜索):
- 顶部搜索框 (手机/昵称/邮箱模糊)
- 列表 item: 头像 + 昵称 + 手机(脱敏) + VIP 状态 chip + admin badge
- 分页: 滚到底加载 + 下拉刷新
- tap item → 跳 `users-detail.vue?user_id=xxx`

新文件 `apps/mp/pages/admin/users-detail.vue`:
- 用户详情 (phone / email / 创建时间 / VIP 剩余天数 / 邀请数)
- Action buttons:
  - **加 VIP 时长** (输入 days + reason, modal 二次确认)
  - **改昵称** (透传 PATCH)
  - **禁用/启用** (status 切换, modal 二次确认)
  - **软删除** (modal 二次确认, 红色按钮)

**关键 UX**:
- 任何写操作都 modal 二次确认 (admin 误点成本高)
- 加 VIP 时长 modal: 显示当前 VIP end_at + "加完后将变为 xxx" 预览
- 软删后跳回列表 + toast "已删除"

**路由声明** (`apps/mp/pages.json`):
```json
{
  "path": "pages/admin/users",
  "style": { "navigationBarTitleText": "用户管理" }
},
{
  "path": "pages/admin/users-detail",
  "style": { "navigationBarTitleText": "用户详情" }
}
```

---

## ✅ 验收标准

| # | Bug | 验证方法 |
|---|---|---|
| 1 | 13007458553 注册或登录后 `GET /me` 返回 `is_admin=true` | curl + 真机登录 |
| 2 | 其它手机号 (e.g. 13800138000) `GET /me` 返回 `is_admin=false` | curl |
| 3 | 非 admin 用户调 `GET /admin/users` 返回 **403 not_admin** | curl + 普通用户 token |
| 4 | admin 用户调 `GET /admin/users?q=张三` 模糊搜索昵称含张三的用户 | curl + admin token |
| 5 | admin 用户调 `POST /admin/users/{id}/grant-vip` 加 30 天, `vip_memberships` 表新增 source=admin_grant 记录 | curl + 查 DB |
| 6 | 管理员手机号在 me 页能看到 "管理员" section + "用户管理" 入口 | 真机 |
| 7 | 普通用户的 me 页**看不到**该 section | 真机 |
| 8 | 用户管理页能搜索/分页/查详情/加 VIP/改昵称/禁用/软删 | 真机端到端 |
| 9 | 软删用户后, 该用户再次登录 401 (status=-1 或 deleted_at IS NOT NULL) | 真机 |
| 10 | tests/test_admin_rbac.py 全过 (require_admin 依赖 + UserPublic.is_admin) | CI |
| 11 | tests/test_admin_users.py 全过 (5 endpoint 端到端) | CI |

---

## 🧪 测试覆盖

新文件 `apps/api/tests/test_admin_rbac.py`:
- `test_get_current_admin_rejects_non_admin` — 普通用户 401/403
- `test_get_current_admin_rejects_no_token` — 无 token 401
- `test_get_current_admin_accepts_admin` — admin 200
- `test_user_public_includes_is_admin_true_for_admin_phone` — 13007458553 注册后 is_admin=true
- `test_user_public_is_admin_false_for_others` — 其它手机号 is_admin=false

新文件 `apps/api/tests/test_admin_users.py`:
- 5 endpoint x 3 场景 (admin 成功 / 普通用户 403 / 不存在 404) = 15 个测试
- `test_grant_vip_extends_membership_with_audit_source` — 加完查 vip_memberships.source
- `test_grant_vip_rejects_days_over_365` — 422
- `test_delete_user_soft_only_sets_deleted_at` — 行还在, deleted_at NOT NULL
- `test_search_by_phone_partial_match` — `?q=130074` 命中
- `test_search_by_nickname_partial_match` — `?q=张` 命中

**覆盖率目标**: 新增代码 ≥ 80% (与 Sprint 9 一致).

---

## 🛡️ 风险 + 回滚

| 风险 | 概率 | 应对 |
|---|:---:|---|
| migration 0017 跑失败 (PG 锁表) | 低 | `is_admin` 列 add 是非阻塞 DDL (PG 11+ 默认), 但 server_default 会扫全表回填. 用户表预期 < 10000 行, 影响 ≤ 5 秒 |
| 13007458553 在 migration 之前没注册过, UPDATE 0 行 → admin 失踪 | 中 | `_maybe_grant_initial_admin` hook 在 verify_phone_login 兜底, 首次登录自动赋权 |
| admin 误删自己 (deleted_at) | 中 | API 拒绝 `user_id == current_admin.user_id` 的 DELETE, 返回 422 `cannot_delete_self` |
| admin 误把自己 is_admin=false (AdminUserUpdate.patch) | 已防 | `AdminUserUpdate` schema 不暴露 is_admin 字段, FE 也没改它的入口 |
| 加 VIP 加错对象 / 加错时长 | 中 | FE 两道 modal: 1) 输入预览 2) "确认加 N 天给 张三?"; BE 还会写 audit log |
| admin 滥用搜索做 PII 嗅探 | 低 | `list_users` 不返 `password_hash` / `payment_*` 字段; mask phone (`138****8000`) ? Sprint 10 先不 mask, Sprint 11 加 |

**回滚预案**:
1. alembic downgrade 0016: `users` 删 `is_admin` 列 — 安全, 不影响业务
2. FE 隐藏 admin section: `git revert` FE-S10-001 那个 commit
3. 5 个 admin endpoint 全部走 `get_current_admin` 依赖, 删依赖 = 全 503; 但留着也无害(普通用户 403)

---

## 📦 上线 checklist

- [ ] `alembic upgrade head` 跑 0017 migration
- [ ] 确认 13007458553 在 DB 里 `SELECT user_id, is_admin FROM users WHERE phone='13007458553'` 返回 `is_admin=true`
- [ ] BE `pytest tests/test_admin_rbac.py tests/test_admin_users.py` 全过
- [ ] mp 端真机扫码 → 13007458553 登录 → me 页有"管理员"section → 进用户管理页 → 搜自己 → 看详情 → 加 VIP → 验证生效
- [ ] mp 端真机扫码 → 普通用户 (e.g. 13800138000) 登录 → me 页**没有**"管理员"section
- [ ] curl 用普通用户 token 调 `GET /admin/users` → 必须 403

---

## 🔮 后续 sprint (Sprint 11 占位)

下个 sprint 会基于本 sprint 的 RBAC 基础设施加 4 个管理页:
- broker 管理页 (CRUD + 开户链接编辑)
- feedback 管理页 (列表/详情/删除)
- community 管理页 (帖子审核/隐藏/删除)
- knowledge 管理页 (富文本编辑/分类/发布开关)

详见 [`spec/28-sprint-11-admin-pages-backlog.md`](./28-sprint-11-admin-pages-backlog.md).

---

## 📐 设计哲学 (本 sprint retro 提前填)

1. **migration 不主动 INSERT user** — 让 13007458553 走正常注册流程, migration 只 UPDATE. 这样如果该手机还没注册过, 不会建空用户行污染表
2. **双套 admin 鉴权机制** — `X-Admin-Token` (ops) + `JWT + is_admin` (in-app). 两者**重叠不冲突**, ops 通道可以救场(admin 账号被禁/手机丢失时仍能用 ops token 远程恢复)
3. **AdminUserUpdate 不开放 is_admin patch** — admin 不能通过这个 endpoint 给自己/别人加权. 加管理员的路径**唯一就是 DB / migration**, 防越权
4. **grant_vip 不幂等** — 与产品定位一致: admin 是运营手动操作, 不是自动化触发器. 幂等只在系统触发器(invite_reward)有意义
5. **Sprint 10 不实现 admin_audit_logs 表** — 暂时只 log 文件, Sprint 11 一起加表 schema; 实测期间 log 文件能复盘就够了
