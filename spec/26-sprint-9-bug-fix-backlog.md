# Sprint 9 — `bug-fix-2305` 5 项 (密码登录 + 微信资料 + UX) (2026-05-06 23:42–2026-05-07 00:30 ✅)

> 状态: ✅ **代码完成** — 全 5 项 bug 已实现; 等待 prod 部署 (alembic 0016 + .env 配置 + 微信小程序后台启用 chooseAvatar)
>
> 用户 2026.05.06 总结上一轮(部署 + 上线)经验后, 提出 5 项产品体验 bug. 涉及
> **登录方式重构** (Bug ① ②) + 3 项 UX 小改 (③ ④ ⑤). 本 sprint 全做完, 工时
> ~3h (代码 + 单测 + 文档).

参考:

- 上游决策: 用户原始 bug 单 [`docs/bug/2026.05.06-bug.md`](../docs/bug/2026.05.06-bug.md)
- 上一 sprint: [`spec/24-sprint-8-multi-source-kol-rollout.md`](./24-sprint-8-multi-source-kol-rollout.md)
- 现有鉴权: [`apps/api/app/services/auth_service.py`](../apps/api/app/services/auth_service.py) (OTP + 微信)
- 现有 me 页: [`apps/mp/pages/me/index.vue`](../apps/mp/pages/me/index.vue) (昵称 / 邀请 / 商务)
- 现有登录页: [`apps/mp/pages/auth/login.vue`](../apps/mp/pages/auth/login.vue) (OTP + 微信)
- 微信新规 chooseAvatar: <https://developers.weixin.qq.com/community/develop/doc/00022c683e8a80b29bed2142b56c01>
- bcrypt 依赖: `passlib[bcrypt]>=1.7.4`

---

## 🎯 用户拍板 (4 项关键决策)

| # | 决策点 | 选项 | 拍板 |
|---|---|---|:---:|
| Q1 | 登录凭据组合 | A 仅手机+密码 / **B 手机+密码 + 邮箱+密码** / C 仅邮箱+密码 / D 三登录方式 | **B** ⭐ |
| Q2 | 微信信息 + 强制补充时机 | **A 本 sprint 一起** / B 拆 sprint 10 / C 仅做头像昵称 | **A** ⭐ |
| Q3 | 密码强度 | **A 6-32 字含数字** / B 8-32 含数字字母 / C 银行级 | **A** ⭐ |
| Q4 | 已有 OTP 老用户迁移 | **A 强制弹设密码引导** / B 我的页加入口不强制 / C 一刀切 | **A** ⭐ |

→ DB schema 一次定型: `users` 表加 `email`, `password_hash`. 老用户登录后强制弹
"设置密码"页, 设完才能继续用. OTP 路径保留作"忘记密码"找回兜底.

---

## 🐛 用户上报 5 项 (`bug-fix-2305`)

| # | 现象 | 严重度 | 修复策略 |
|---|------|:----:|---|
| ① | 手机/邮箱+密码登录;mp 端微信登录修复 | **P0 大改** | 加 `email` + `password_hash` 字段;新增 `register/password` + `login/password` endpoint;mp 端 `wechat_mp_app_id/secret` 是 `.env` 配置问题不是代码 bug, 加文档 |
| ② | 微信登录后获取头像/昵称 + 强制补手机/邮箱+密码 | **P1 大改** | mp 用新规 `<button open-type="chooseAvatar">` + `<input type="nickname">`;`UserPublic` 加 `profile_complete` 字段;前端首登时自动跳"完善资料"页 |
| ③ | 首页右上角去掉登录注册按钮 | **P0 小改** | 删 hero 内 `v-if="!loggedIn"` 的 `auth-pill` 5 行 (反正所有 tab 未登录都 reLaunch 到 login, 这按钮冗余) |
| ④ | 改昵称后退出再登录显示原昵称 | **P0 调查** | 加 `authStore.refreshUser()` action 兜底从 `GET /me` 拉最新;`me 页 onShow` 主动 refresh;e2e 测试 |
| ⑤ | 绑定邀请人边上加 ⓘ 解释邀请福利 | **P2 UX** | 后端加 `GET /api/v1/invite/reward-config`;`me 页` section header 加 ⓘ chip + `uni.showModal` 弹窗 |

---

## 🔬 各 Bug 实施细节

### Bug ① — 密码登录 (双方式: 手机+密码 / 邮箱+密码)

#### DB schema (Alembic 0016)

```sql
ALTER TABLE users ADD COLUMN email VARCHAR(254) NULL;
ALTER TABLE users ADD COLUMN password_hash VARCHAR(60) NULL;  -- bcrypt 固定 60 字符
CREATE UNIQUE INDEX uq_users_email ON users(email) WHERE email IS NOT NULL;
```

- `email` UNIQUE WHERE NOT NULL: 允许多个用户都没邮箱(微信用户), 但有邮箱时必须唯一
- `password_hash` 固定 60 字符 (bcrypt 输出: `$2b$12$...` = 7 字符 cost prefix + 22 字符 salt + 31 字符 hash)
- 不加 `password_set_at` 字段(`updated_at` 已经能反映)

#### BE 依赖

```toml
# pyproject.toml [project.dependencies] 加:
"passlib[bcrypt]>=1.7.4"
```

`bcrypt` cost factor 默认 12 (~250ms/次, 平衡安全 + UX); MVP 期不调.

#### BE schemas (`app/schemas/auth.py` 扩展)

```python
class PasswordRegisterRequest(BaseModel):
    """注册时需要 phone OR email 二选一 + password + (optional) referral code."""
    phone: str | None = Field(None, min_length=8, max_length=20)
    email: EmailStr | None = None
    password: str = Field(..., min_length=6, max_length=32)
    invite_code: str | None = None  # 注册时也可顺便绑邀请人

    @model_validator(mode="after")
    def at_least_one_credential(self) -> Self:
        if not self.phone and not self.email:
            raise ValueError("phone 或 email 至少填一个")
        if not any(c.isdigit() for c in self.password):
            raise ValueError("密码必须至少包含一个数字")
        return self


class PasswordLoginRequest(BaseModel):
    """登录时只需要 identifier (phone 或 email) + password."""
    identifier: str = Field(..., min_length=4, max_length=254, description="手机号或邮箱")
    password: str = Field(..., min_length=6, max_length=32)


class SetPasswordRequest(BaseModel):
    """老用户设置密码 / 重置密码 (PUT /me/password)."""
    password: str = Field(..., min_length=6, max_length=32)
    # 已有密码用户改密码时必填; 首次设密码可不填
    current_password: str | None = Field(None, min_length=6, max_length=32)


# UserPublic 扩展 — 让前端能判断 profile_complete
class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: uuid.UUID
    nickname: str | None
    avatar_url: str | None
    region: str
    invite_code: str
    status: int
    created_at: datetime
    # Sprint 9 新增 ↓
    has_phone: bool      # phone IS NOT NULL
    has_email: bool      # email IS NOT NULL
    has_password: bool   # password_hash IS NOT NULL
    has_wechat: bool     # wechat_openid IS NOT NULL
    profile_complete: bool  # (has_phone OR has_email) AND has_password
```

#### BE service 改动 (`auth_service.py`)

3 个新函数:

```python
async def register_with_password(
    session, *, phone=None, email=None, password, invite_code=None
) -> tuple[User, IssuedTokens]:
    """注册新用户 — phone 或 email 二选一. 已存在直接抛 PhoneAlreadyExistsError /
    EmailAlreadyExistsError, 让用户去登录页. invite_code 可顺便绑."""

async def verify_password_login(
    session, *, identifier, password
) -> tuple[User, IssuedTokens]:
    """密码登录 — identifier 自动判断 phone (digit only) vs email (含 @).
    bcrypt verify; 错误统一抛 InvalidCredentialsError (不暴露是 user 不存在还是
    密码错, 防 enumeration attack)."""

async def set_user_password(session, user, *, password, current_password=None) -> None:
    """老用户设密码 / 改密码. 已有密码必须验证 current_password."""
```

#### BE endpoints (`api/v1/auth.py` + `api/v1/me.py`)

```python
# auth.py 新增 2 个 endpoint
POST /api/v1/auth/register/password  # PasswordRegisterRequest -> LoginResponse
POST /api/v1/auth/login/password     # PasswordLoginRequest -> LoginResponse

# me.py 新增 1 个 endpoint
PUT /api/v1/me/password              # SetPasswordRequest -> {ok: true}
```

错误码:

```
| HTTP | code                     | 触发条件                      |
| 400  | password_too_weak        | 不含数字 / 长度不够          |
| 400  | invalid_email_format     | 邮箱格式错                    |
| 400  | identifier_required      | phone / email 都没填          |
| 401  | invalid_credentials      | 密码错 / 用户不存在 (统一)   |
| 401  | current_password_invalid | PUT /me/password 旧密码错    |
| 409  | phone_already_exists     | 注册时 phone 已被占          |
| 409  | email_already_exists     | 注册时 email 已被占          |
| 429  | password_login_rate_limited | 5 次/5min 同 identifier   |
```

#### FE api 扩展 (`api/auth.ts`)

```ts
export function registerPassword(req: PasswordRegisterRequest): Promise<LoginResponse>
export function loginPassword(req: PasswordLoginRequest): Promise<LoginResponse>
export function setPassword(req: SetPasswordRequest): Promise<{ ok: true }>
```

#### FE UI (`pages/auth/login.vue` 改造)

3 个 tab 改 4 个 (按用户主流频次排序):

```
[密码登录] [短信验证码] [微信一键登录(仅mp)] [立即注册]
```

- **密码登录**: identifier (phone/email 自动识别) + password + "登录" + "忘记密码?" 链接(走 OTP)
- **短信验证码**: 兼容老用户 / 找回密码兜底, 与 Sprint 1 一致
- **微信一键登录**: 仅 mp, 与 Sprint 1 一致
- **立即注册**: 跳 `/pages/auth/register` 新页

新页 `pages/auth/register.vue`:
```
[手机号 ↔ 邮箱] segment 二选一  (默认手机号)
[输入框: 手机号 / 邮箱]
[密码 + 确认密码]
[(可选) 邀请码]
[协议勾选]
[注册]
```

### Bug ② — 微信信息授权 + 强制补充流程

#### 微信端 chooseAvatar / nickname 新规 (mp-weixin)

`wx.getUserProfile` 在基础库 2.27.1 起被移除. 新方案:

```vue
<!-- chooseAvatar (用户主动点 button) -->
<button open-type="chooseAvatar" @chooseavatar="onChooseAvatar">
  <image :src="avatarUrl" class="avatar-preview" />
</button>

<!-- nickname 输入 (微信会自动审核敏感词) -->
<input type="nickname" placeholder="请填写昵称" @blur="onNicknameInput" />
```

`onChooseAvatar` 拿到的是临时本地 path, 必须**上传到我们 OSS / BE 后**才有 https URL.

MVP 简化: 上传到 BE 的 `POST /api/v1/me/avatar` (multipart) 走 disk 存储 +
返回 https URL → BE 写 `users.avatar_url`. 后续可换 OSS.

#### BE 新增 endpoints

```python
# api/v1/me.py
PUT  /api/v1/me/password         # Bug ① 已规划
POST /api/v1/me/avatar           # 接收 multipart, 存 disk, 写 avatar_url
PATCH /api/v1/me                 # 已有, 扩展支持 email + avatar_url 字段
```

#### FE 完善资料页 (`pages/auth/profile-complete.vue` 新增)

登录后(无论密码 / OTP / 微信), 检查 `user.profile_complete`. 不完整 → reLaunch 这个页:

```
进度条 (Step 1/3 / 2/3 / 3/3, 跟现状自动判断)
─ Step 1: 头像 + 昵称 (mp 用 chooseAvatar, h5/app 用普通 input + 默认头像)
─ Step 2: 手机号 / 邮箱 二选一 (复用 register 页 segment)
─ Step 3: 设置密码
[完成 → reLaunch /pages/index/index]
```

按当前 `user` 字段自动跳过已完成的 Step:
- `nickname IS NOT NULL && avatar_url IS NOT NULL` → skip Step 1
- `(phone OR email) IS NOT NULL` → skip Step 2
- `password_hash IS NOT NULL` → skip Step 3

全完后 → reLaunch 首页, 用户进入正常流程.

#### 兼容老用户

OTP 老用户登录 → BE 返 `profile_complete=false (has_password=false)` → FE 自动跳完善
资料页 → 直接进 Step 3 设密码 → 完成进首页.

### Bug ③ — 删首页 hero 右上角 auth-pill (~5min)

```diff
- <view v-if="!loggedIn" class="auth-pill" @tap="gotoLogin">
-   <text>登录 / 注册</text>
- </view>
+ (整段删除)
```

清理 `.auth-pill` CSS + `gotoLogin` function 引用 (gotoLogin 还有别处用, 不删函数).

### Bug ④ — 昵称持久化(主排查 + 加兜底)

**根因排查** (5 处看链路完整, 但加防御性代码确保不出错):

| 链路 | 现状 | 兜底加固 |
|---|---|---|
| `PATCH /me` BE | `commit + refresh` ✓ | — |
| `updateMe` 返回 | UserPublic ✓ | — |
| `authStore.setUser(updated)` | `saveUser` 写 storage ✓ | — |
| 退出 `clearAuth` 清掉 user storage | ✓ | — |
| 重登 `verify_phone_login` 拿最新 user | `find_user_by_phone` ✓ | — |
| **可能问题**: hydrate 时 storage 旧 user 早于 setSession | 理论不应该 | **加 `refreshUser` action: me 页 onShow 主动拉 GET /me 兜底** |

#### 实施

```ts
// stores/auth.ts 新增 action
async function refreshUser(): Promise<UserPublic | null> {
  if (!loggedIn.value) return null
  try {
    const u = await fetchMe()  // 新增 api/auth.ts: fetchMe
    setUser(u)
    return u
  } catch (e) {
    console.warn('[auth] refreshUser failed', e)
    return null  // 不阻塞
  }
}

// pages/me/index.vue refreshAuthGate 加一行
function refreshAuthGate() {
  if (!loggedIn.value) { uni.reLaunch(...); return }
  upgrade.close()
  void authStore.refreshMembership()
  void authStore.refreshUser()  // ★ 新增: 兜底拉最新 user (含 nickname)
  ...
}
```

加 e2e 测试 `tests/integration/test_nickname_persist_e2e.py`:
1. 注册用户 → 改昵称为 "AAA" → GET /me 应返 "AAA"
2. logout → login → GET /me 仍应返 "AAA"

### Bug ⑤ — 绑定邀请人 ⓘ 福利说明 (~15min)

#### BE 新增 endpoint

```python
# api/v1/invite.py
@router.get("/reward-config", response_model=InviteRewardConfig)
async def get_reward_config():
    """返当前邀请奖励配置, 让 FE 渲染'邀请 N 人 → +M 天 VIP'文案."""
    s = get_settings()
    return InviteRewardConfig(
        threshold_n=s.invite_reward_n_users,
        vip_days=s.invite_reward_vip_days,
    )
```

#### FE 改动 (`pages/me/index.vue`)

```vue
<view class="section-header">
  <view class="section-title-row">
    <text class="section-title">绑定邀请人</text>
    <view class="info-icon" @tap.stop="showInviteRewardInfo">ⓘ</view>
  </view>
  <text class="section-subtitle">仅可绑定一次, 不可更改</text>
</view>
```

```ts
async function showInviteRewardInfo() {
  if (!rewardCfg.value) {
    rewardCfg.value = await fetchInviteRewardConfig()
  }
  uni.showModal({
    title: '邀请福利',
    content: `成功邀请 ${rewardCfg.value.threshold_n} 位好友注册, 即可获得 +${rewardCfg.value.vip_days} 天 VIP 时长. 邀请 ${rewardCfg.value.threshold_n * 2} 位 → +${rewardCfg.value.vip_days * 2} 天, 以此类推.`,
    showCancel: false,
    confirmText: '我知道了',
  })
}
```

---

## 📦 实现交付清单 (待完成)

### BE 改动

| 文件 | 改动 | 行数 |
|---|---|:---:|
| `alembic/versions/0016_users_password_email.py` | **新增** Bug ① ② DB schema | ~60 |
| `app/db/models/user.py` | 加 `email` + `password_hash` 字段 | +6 |
| `app/schemas/auth.py` | UserPublic 扩展 + 3 新 request schemas | +90 |
| `app/services/auth_service.py` | 3 新函数 register_password / verify_password_login / set_user_password | +180 |
| `app/services/user_service.py` | `find_user_by_email` | +10 |
| `app/services/security_password.py` | **新增** bcrypt 包装 | +60 |
| `app/api/v1/auth.py` | 2 新 endpoints (register/password + login/password) | +120 |
| `app/api/v1/me.py` | PUT /password + POST /avatar + PATCH /me 加 email/avatar_url | +120 |
| `app/api/v1/invite.py` | GET /reward-config | +25 |
| `app/schemas/invite.py` | InviteRewardConfig | +20 |
| `app/schemas/me.py` | UpdateMeRequest 加 email + avatar_url | +15 |
| `pyproject.toml` | passlib[bcrypt] 依赖 | +1 |
| `tests/test_password_auth.py` | **新增** 单测 | +280 |
| `tests/integration/test_nickname_persist_e2e.py` | **新增** e2e | +90 |

### FE 改动

| 文件 | 改动 |
|---|---|
| `apps/mp/api/auth.ts` | registerPassword + loginPassword + setPassword + fetchMe + UserPublic 加新字段 |
| `apps/mp/api/invite.ts` | fetchInviteRewardConfig + InviteRewardConfig type |
| `apps/mp/stores/auth.ts` | refreshUser action |
| `apps/mp/pages/auth/login.vue` | 4 tab 改造 (密码 / OTP / 微信 / 注册入口) |
| `apps/mp/pages/auth/register.vue` | **新增** 注册页 |
| `apps/mp/pages/auth/profile-complete.vue` | **新增** 完善资料页 (3 step 自动跳过) |
| `apps/mp/pages/index/index.vue` | 删 hero auth-pill (Bug ③) |
| `apps/mp/pages/me/index.vue` | 邀请人加 ⓘ + 弹窗 (Bug ⑤) + refreshUser 兜底 (Bug ④) |
| `apps/mp/pages.json` | 注册 register / profile-complete 2 新页 |

### DOC

| 文件 | 改动 |
|---|---|
| `spec/26-sprint-9-bug-fix-backlog.md` | **新增** — 本文档 |
| `docs/bug/2026.05.06-bug.md` | 5 项标 ✅ + 修复方案摘要 |

---

## 📋 Lessons Learned (Sprint 9 retro)

1. **`passlib[bcrypt]` 与 `bcrypt 5.x` 不兼容**: passlib 1.7.4 内部 `detect_wrap_bug`
   在 bcrypt 5.x 严格 72 字节限制下抛 ValueError, 导致 `CryptContext` 实例化失败.
   解法是绕过 passlib, 直接用 `bcrypt.hashpw` / `bcrypt.checkpw` + 自己包
   `hash_password / verify_password` (60 行代码搞定). passlib 维护已经停滞,
   长期看自己包反而更稳.

2. **vue-tsc 报 `<input type="nickname">` 的 `onBlur` 类型不兼容**: nickname 是
   微信小程序专属属性, vue-tsc 用 HTML5 input 类型推断 onBlur 应该是 `FocusEvent`,
   但 mp 端实际事件 e 是 `{ detail: { value: string } }`. 解决: handler 签名用
   `unknown` 兜底, 运行时 type-narrow 摘 mp 形态 (与 H5 双兼容; H5 端不会触发该 handler).

3. **`UserPublic._derive_has_flags`** 的 ORM 路径用 `getattr(data, "user_id", None)`
   而不是 `data.user_id`, 让 mypy 在 ORM 实例 / dict 双兼容时不抛 attr-defined.
   付出的代价是丢一点 type safety, 但 schema validator 本来就是 boundary, 用
   getattr fallback 是 trade-off 合理.

4. **`profile_complete` 派生字段** 一开始想 schema 推 enum (FE 自己算
   `has_password & (has_phone | has_email)`), 后改成 BE 派生固化 → FE 直接用
   `resp.user.profile_complete` 决策跳转, 减少 FE/BE 业务规则双向漂移.
   后续若改规则 (例: 强制头像), 改 BE 一处即可.

5. **`UpdateMeRequest` PATCH 加 email** 的 race 防御: Pydantic 校验通过 +
   唯一性查通过 + commit 时 unique 仍可能撞车 (并发场景, 两个用户同时设同邮箱).
   加 try/except 拦 commit 异常 → 409 兜底, 让用户感知"邮箱被抢了"而不是看 5xx.

6. **avatar 上传走 `asyncio.to_thread`** 而不是 anyio.Path: ruff ASYNC240 提示
   "async 函数别用 pathlib 同步操作". 引入 anyio.Path 单点收益不大, 反而拖了
   依赖路径. 用 `asyncio.to_thread(_write_to_disk)` 把整段 mkdir + write_bytes
   包进 thread pool 一样达到非阻塞效果, 与项目其它地方风格一致.

7. **`profile-complete.vue` 自适应 step**: 用 `remainingSteps` computed 实时算,
   每次提交完一步它会自动短一格, 所以 `currentStepIdx = 0` 重新指向第一个未完成
   的就行 (而不是 idx+1). 这种"声明式自动跳过已完成"比"命令式 step 流转" 简洁
   一倍, 老用户 (只缺密码) 进来直接 1 step 完事, 新微信用户进来 3 step 走完.

8. **手机号补绑** 在 profile-complete Step 2 没做, 用模态引导用户改用邮箱或
   logout 重登 OTP. 原因: 手机号补绑需要 OTP 验证 (防止给别人手机绑账号),
   而 profile-complete 是已登录态, 走 OTP 会把整个流程复杂化. Sprint 10+ 加
   独立的 `POST /me/phone/bind` (送 OTP + verify) 解决.

---

## 🔄 后续 (Sprint 10+ 待用户拍板)

- [ ] 阿里云 SMS 真实接入 (用户走完公司资质后) — 老 OTP 路径自动激活, 无需代码改动
- [ ] OSS 接入: `POST /me/avatar` 当前走 BE disk, 后续切阿里云 OSS / 七牛
- [ ] 邮箱验证: 注册时不验邮箱(MVP 信任), 后续加邮箱验证 link 防恶意注册
- [ ] H5 端微信 OAuth (公众号登录) — 当前 H5 只支持手机/邮箱+密码
- [ ] 第三方登录扩展: Apple / Google (海外用户)
