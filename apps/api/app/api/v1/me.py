"""当前用户路由 (BE-003 / BE-S5-003 / BUG-S6.8-002 / BUG-S9-001 / BUG-S9-002).

Sprint 1: ``GET /me``
Sprint 5 BE-S5-003: ``DELETE /me`` (PIPL §47 注销账号)
Sprint 6.8 BUG-S6.8-002: ``PATCH /me`` (昵称编辑)
Sprint 9 BUG-S9-001: ``PUT /me/password`` (设置/修改密码)
Sprint 9 BUG-S9-001/002: ``PATCH /me`` 扩 email + avatar_url
Sprint 9 BUG-S9-002: ``POST /me/avatar`` (multipart 上传, mp chooseAvatar 使用)
"""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import rate_limit
from app.core.config import get_settings
from app.db import get_session
from app.db.models import User
from app.schemas.auth import SetPasswordRequest, UserPublic
from app.schemas.me import DeleteMeRequest, DeleteMeResponse, UpdateMeRequest
from app.security import (
    ACCESS_TOKEN_TYPE,
    AccessTokenPayload,
    InvalidTokenError,
    TokenExpiredError,
    decode_token,
    get_current_user,
)
from app.services import auth_service, user_deletion_service
from app.services.auth_service import (
    CurrentPasswordInvalidError,
    EmailAlreadyExistsError,
    PasswordNotSetError,
)
from app.services.user_deletion_service import UserAlreadyDeletedError
from app.utils.email import InvalidEmailError, normalize_email

router = APIRouter(prefix="/me", tags=["me"])


# ─── GET /me (BE-003) ─────────────────────────────────────────────


@router.get(
    "",
    response_model=UserPublic,
    status_code=status.HTTP_200_OK,
    summary="当前用户基本信息",
    responses={401: {"description": "未登录 / token 无效 / token 过期"}},
)
async def read_me(current_user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic.model_validate(current_user)


# ─── PATCH /me (BUG-S6.8-002 资料编辑) ────────────────────────────


@router.patch(
    "",
    response_model=UserPublic,
    status_code=status.HTTP_200_OK,
    summary="编辑当前用户资料 (nickname / email / avatar_url)",
    responses={
        400: {"description": "字段校验失败 (空昵称 / 超长 / 邮箱格式 / URL)"},
        401: {"description": "未登录 / token 无效 / token 过期"},
        409: {"description": "邮箱已被其他账号占用"},
    },
)
async def update_me(
    body: UpdateMeRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserPublic:
    """编辑当前用户基本资料.

    Sprint 6.8: 仅 ``nickname``.
    BUG-S9-001: 扩 ``email`` (微信用户首次补充邮箱); 已存在他人占用 → 409.
    BUG-S9-002: 扩 ``avatar_url`` (微信 ``chooseAvatar`` 拿到的临时图地址,
                FE 已 ``uploadFile`` 到 OSS 后再调本接口写库; 这里只校验 URL 字符).

    业务规则:
    - 昵称去首尾空白后必须 1-20 字 (中英文混算)
    - 邮箱走 Pydantic ``EmailStr`` + ``normalize_email`` (lowercase + strip)
    - 头像 URL 长度 ≤ 1024, 必须 https:// 或 http:// (不限域名 — 上传后由
      OSS / CDN 控制, 这里不重复校验)
    - 不传字段或传 None 视为不改 — ``exclude_unset`` 抓非空 patch
    - 字段全空 → 400 (避免无意义请求)
    """
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "no_change",
                "message": "请求未指定要修改的字段",
            },
        )

    if "nickname" in patch:
        new_nickname = (patch["nickname"] or "").strip()
        if not new_nickname:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "nickname_empty",
                    "message": "昵称不能为空",
                },
            )
        if len(new_nickname) > 20:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "nickname_too_long",
                    "message": "昵称最长 20 字",
                },
            )
        current_user.nickname = new_nickname

    if "email" in patch:
        # Pydantic ``EmailStr`` 已通过 → 这里只 normalize + 唯一性校验
        try:
            new_email = normalize_email(patch["email"])
        except InvalidEmailError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "email_format_invalid", "message": str(e)},
            ) from e
        # 容错: 同邮箱重复 PATCH 视为 no-op (不查库, 不写)
        if new_email != (current_user.email or ""):
            from app.services.user_service import find_user_by_email

            existing = await find_user_by_email(session, new_email)
            if existing is not None and existing.user_id != current_user.user_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "email_already_exists",
                        "message": "该邮箱已被其他账号占用",
                    },
                )
            current_user.email = new_email

    if "avatar_url" in patch:
        new_avatar = (patch["avatar_url"] or "").strip()
        # 允许空字符串 → 清头像; 否则必须 http(s):// 开头 + ≤ 1024
        if new_avatar:
            if not (new_avatar.startswith("https://") or new_avatar.startswith("http://")):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "avatar_url_invalid",
                        "message": "头像地址必须 http(s):// 开头",
                    },
                )
            if len(new_avatar) > 1024:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "avatar_url_too_long",
                        "message": "头像地址过长 (最长 1024 字符)",
                    },
                )
        current_user.avatar_url = new_avatar or None

    # 注: update_me 没走 service 层 (单字段 patch 不必抽象), 由 endpoint 自己控制
    # 事务边界: flush 触发 ORM SQL 写入 + commit 落库 (`get_session` 默认不 commit).
    # bug-2305-v2 retro 8.4: 历史只 flush 没 commit, 导致 200 OK 但下次 GET /me 读不到.
    try:
        await session.commit()
    except Exception:  # 兜底 unique 撞车 (race 条件, email 在 commit 时被另请求抢占)
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "email_already_exists",
                "message": "该邮箱已被其他账号占用",
            },
        ) from None
    await session.refresh(current_user)
    logger.info(
        f"me.update.ok user_id={current_user.user_id} fields={list(patch.keys())}"
    )
    return UserPublic.model_validate(current_user)


# ─── PUT /me/password (BUG-S9-001 设置/修改密码) ────────────────────


def _password_set_rate_limit_key(**kwargs: object) -> str:
    """同用户 5次/小时 防被旁人改密 (设置场景: current_password=None 时不限,
    修改场景: 5次/小时已够). 用 user_id 做 key.

    注意: rate_limit 装饰器在 FastAPI endpoint 上调用 key_func 时, 全部走 **kwargs
    (因为 FastAPI dependency injection 都是 keyword 参数). 这里不能写位置参数
    (旧版 ``_: SetPasswordRequest`` 会报 missing 1 required positional argument).
    参见 ``auth.py`` 的 ``_password_register_rate_limit_key`` (那里 endpoint 参数
    叫 ``req``, 所以 key_func 也用 ``req`` 同名 + ``**_`` 兜底, 但那是同名匹配,
    本 endpoint 参数叫 ``body`` 不是 ``req``, 用 ``**kwargs`` 全收最简单).
    """
    user = kwargs.get("current_user")
    if isinstance(user, User):
        return f"user:{user.user_id}"
    return "user:anon"


@router.put(
    "/password",
    response_model=UserPublic,
    status_code=status.HTTP_200_OK,
    summary="设置或修改密码 (新密码必填; 已设过则需 current_password)",
    responses={
        400: {"description": "新密码格式不合法 (长度/复杂度)"},
        401: {"description": "未登录 OR 旧密码错"},
        409: {"description": "已设过密码但未传 current_password"},
        429: {"description": "1 小时内同账号修改密码次数过多"},
    },
)
@rate_limit(
    times=5,
    per_seconds=3600,
    namespace="password_set",
    key_func=_password_set_rate_limit_key,
)
async def set_or_change_password(
    body: SetPasswordRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserPublic:
    """设置或修改密码.

    场景:
    - **首次设置** (老 OTP 用户 / 新微信用户): ``password_hash IS NULL`` →
      不需 ``current_password`` (传了也忽略)
    - **修改密码**: ``password_hash`` 已存在 → 必须传 ``current_password``;
      bcrypt verify 失败返 401

    成功后:
    - 走 ``set_user_password`` 写 hash + commit
    - 不主动吊销其它 session — 由产品决定 (后续 BUG-S9-006 加 "其它设备下线"
      可选项, 现阶段简单点只刷新当前)

    安全:
    - rate limit 5次/小时/账号 (和 ``password_login`` 分桶, 防止恶意 lock 用户)
    - bcrypt verify 走常量时间; 旧密码错也按统一 401 防 info leak
    """
    try:
        await auth_service.set_user_password(
            session,
            user=current_user,
            password=body.password,
            current_password=body.current_password,
        )
    except CurrentPasswordInvalidError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "current_password_invalid",
                "message": "原密码错误",
            },
        ) from e
    except PasswordNotSetError:
        # 不该发生 (already_set 才会要求 current_password); 防御性
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "password_not_set",
                "message": "尚未设置密码, 不需要传原密码",
            },
        ) from None
    except EmailAlreadyExistsError:
        # set_user_password 不应抛, 防御性
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "conflict", "message": "状态异常, 请重试"},
        ) from None
    except ValueError as e:
        # PasswordTooLongError / 格式错统一 400 (Pydantic 已校验过格式, 这里只兜 bcrypt 长度)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "password_format_invalid", "message": str(e)},
        ) from e

    # ⚠ 必须显式 commit (set_user_password 内部只 flush, 不 commit). 历史 b5b71eb 同款.
    await session.commit()
    await session.refresh(current_user)
    logger.info(f"me.password.set.ok user_id={current_user.user_id}")
    return UserPublic.model_validate(current_user)


# ─── DELETE /me (BE-S5-003 PIPL §47 注销账号) ─────────────────────


def _extract_bearer_token(request: Request) -> str | None:
    """从 ``Authorization: Bearer xxx`` 抓 token; ``get_current_user`` 已校验过 header 合法,
    这里复用 (与 auth.py logout 同款实现).
    """
    raw = request.headers.get("authorization")
    if not raw:
        return None
    parts = raw.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _resolve_client_ip(request: Request) -> str | None:
    """从 X-Forwarded-For (取首个) 或 request.client.host 抓 IP.

    与 ``feedback_service`` 同款; 反代后 X-Forwarded-For 是真实 IP, 直接 client.host
    会拿到反代 IP. 信任 header 是因为反代会重写, 没反代的 dev 直接用 client.host.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client is not None:
        return request.client.host
    return None


@router.delete(
    "",
    response_model=DeleteMeResponse,
    status_code=status.HTTP_200_OK,
    summary="注销账号 (PIPL §47): 软删 + 30d 后真删 PII",
    responses={
        401: {"description": "未登录 / token 无效 / token 过期"},
        409: {"description": "用户已注销过 (重复请求)"},
    },
)
async def delete_me(
    request: Request,
    body: DeleteMeRequest | None = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DeleteMeResponse:
    """注销账号: 标 soft delete + audit + 拉黑当前 access + 吊销所有 refresh.

    流程:
    1. ``soft_delete_user`` 在同事务里完成所有 DB 改动 (users / auth_sessions /
       invite_codes / user_deletions audit) + Redis 黑名单
    2. 实际 PII 真删由 30d 后 cron (``user_deletion_service.run_hard_delete_pii_job``) 跑
    3. 客户端必须本地清 token, 跳转登录页 — 后续请求 ``get_current_user`` 会因
       ``status=0`` 或 ``token_revoked`` 401 拒绝

    PIPL 合规要点:
    - ``deleted_at`` 标在 user row, 不立即清字段 — 让 30d 内能反悔 (但本 PR 没做"撤回",
      留 5.5+ 加 ``POST /me/restore``; 现阶段误注销只能联系客服)
    - 30d 后 cron 真删 phone / wechat_* / apple_id / nickname / avatar_url
      所有 PII 字段, 保留 user_id / region / 时间戳 (财务 / 渠道审计要)
    - 反馈 / VIP 订单 / conversion_events 不删 (财务监管 7 年留存; 已无 PII)
    """
    body = body or DeleteMeRequest()

    # 拆 access payload (用来拉黑 jti). 拿不到不阻断主路径 — 极端容忍 (反正 user.status=0
    # 已经能让所有后续请求 401)
    raw_token = _extract_bearer_token(request)
    access_payload: AccessTokenPayload | None = None
    if raw_token:
        try:
            decoded = decode_token(raw_token, expected_type=ACCESS_TOKEN_TYPE)
        except (InvalidTokenError, TokenExpiredError):
            decoded = None
        if isinstance(decoded, AccessTokenPayload):
            access_payload = decoded

    if access_payload is None:
        # 不该发生 (get_current_user 已经走过同款 decode), 但兜底; 没法拉黑 jti 时
        # 至少把 user.status=0 写下来, 让其它请求失败
        logger.warning(
            f"me.delete.no_payload user_id={current_user.user_id} "
            "(get_current_user 通过但 payload 解析失败?)"
        )

    ip = _resolve_client_ip(request)
    user_agent = request.headers.get("user-agent")

    try:
        result = await user_deletion_service.soft_delete_user(
            session,
            user=current_user,
            access_payload=access_payload,  # type: ignore[arg-type]
            reason=body.reason,
            ip=ip,
            user_agent=user_agent,
        )
    except UserAlreadyDeletedError as e:
        # 极端: get_current_user 已检查 status=1, 但并发场景理论可能撞 (两个请求同时调)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "user_already_deleted",
                "message": "账号已经注销, 请勿重复请求",
            },
        ) from e

    # 路由层 get_session 在正常返回时 commit; 异常自动 rollback (不需要手动)

    return DeleteMeResponse(
        deleted=True,
        user_id=result.user_id,
        deleted_at=result.deleted_at,
        real_purge_scheduled_at=result.real_purge_scheduled_at,
        audit_id=result.audit_id,
    )


# ─── POST /me/avatar (BUG-S9-002 微信 chooseAvatar 上传) ─────────


# 允许的 image MIME → 落地扩展名 map. 不接受其它类型, 即使后端 Pillow 能解码也拒绝
# (避免 svg 嵌脚本 / webp 兼容杂碎)
_ALLOWED_AVATAR_MIME: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def _avatar_rate_limit_key(**kwargs: object) -> str:
    """同用户 10 次/小时上传头像; 防恶意刷文件占满 disk.

    rate_limit key_func 在 FastAPI endpoint 上必须 ``**kwargs`` (dependency
    injection 走 keyword), 不能写裸位置参数 — 见 ``_password_set_rate_limit_key`` 注释.
    """
    user = kwargs.get("current_user")
    if isinstance(user, User):
        return f"user:{user.user_id}"
    return "user:anon"


@router.post(
    "/avatar",
    response_model=UserPublic,
    status_code=status.HTTP_200_OK,
    summary="上传当前用户头像 (multipart, mp chooseAvatar 用)",
    responses={
        400: {"description": "MIME 类型不支持 / 文件为空"},
        413: {"description": "文件超过 ``avatar_max_bytes`` 限制"},
        429: {"description": "1 小时内同用户上传 > 10 次"},
        503: {"description": "服务端未配置 ``avatar_public_base_url`` (运维问题)"},
    },
)
@rate_limit(
    times=10,
    per_seconds=3600,
    namespace="avatar_upload",
    key_func=_avatar_rate_limit_key,
)
async def upload_avatar(
    file: UploadFile = File(..., description="头像图片 (jpg/png/webp, ≤ 2 MiB)"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserPublic:
    """接收 multipart 上传的头像图, 写本地 disk + 拼公网 URL → 写库 ``users.avatar_url``.

    流程:
    1. 校验 MIME (jpg/png/webp 白名单) + 大小 (默认 ≤ 2 MiB, 见 settings)
    2. 写 ``{avatar_storage_dir}/{user_id}/{token_hex}.{ext}`` (旧文件不删, 防止跨设备
       缓存击穿; 旧 URL 留库做兜底访问, 后续 PIPL 注销时统一清)
    3. 拼 ``{avatar_public_base_url}/{user_id}/{filename}`` 写 ``users.avatar_url``
    4. 返回 refreshed UserPublic — FE 直接 ``setUser(resp)`` 即可

    安全 / 运维:
    - 文件名走 ``token_hex(8)`` (16 字符) 避免可猜测; 不复用原文件名 (防 path traversal /
      恶意扩展名)
    - 写入前 ``mkdir(parents=True, exist_ok=True)`` 确保用户目录存在
    - rate_limit 10 次/小时/用户, 防恶意刷 disk
    - MVP 走本地 disk; 生产建议反代 (nginx) 直接走 OSS / CDN 路径, 这里只是 fallback
    """
    settings = get_settings()
    if not settings.avatar_public_base_url:
        # 运维没配公网 base URL 时, 拒服务 — 防止 dev 环境误用 file:// 路径
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "avatar_storage_unconfigured",
                "message": "头像上传服务未配置, 请联系管理员",
            },
        )

    content_type = (file.content_type or "").lower().strip()
    ext = _ALLOWED_AVATAR_MIME.get(content_type)
    if not ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "avatar_mime_unsupported",
                "message": f"仅支持 jpg / png / webp; 收到 {content_type or '未知'}",
            },
        )

    # 读取 + 大小校验. 用 1 MiB chunk 边读边累加, 超限立即 413, 不读完整个 body
    chunks: list[bytes] = []
    total = 0
    chunk_size = 1024 * 1024
    max_bytes = settings.avatar_max_bytes
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "code": "avatar_too_large",
                    "message": f"头像文件最大 {max_bytes // 1024 // 1024} MiB",
                },
            )
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "avatar_empty", "message": "头像文件为空"},
        )

    # ruff ASYNC240 提示 "async 里别直接用 pathlib 同步操作"; 但本仓库 BE 走 thread-pool
    # 模型 (FastAPI starlette ASGI worker), 文件 < 2 MiB 阻塞极短 (~ms 级 ext4 fsync).
    # 整段 mkdir + write_bytes 包进 to_thread, 把 path 计算也搬过去. 引入 anyio.Path 仅为
    # 单点 lint 收益不大, 反而引入新的依赖路径.
    avatar_storage_dir = settings.avatar_storage_dir
    user_id_str = str(current_user.user_id)
    filename = f"{secrets.token_hex(8)}.{ext}"
    blob = b"".join(chunks)

    def _write_to_disk() -> None:
        storage_dir = Path(avatar_storage_dir).resolve() / user_id_str
        storage_dir.mkdir(parents=True, exist_ok=True)
        (storage_dir / filename).write_bytes(blob)

    try:
        await asyncio.to_thread(_write_to_disk)
    except OSError as e:
        logger.error(f"avatar.write.fail user_id={current_user.user_id} {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "avatar_storage_failed",
                "message": "头像保存失败, 请稍后重试",
            },
        ) from e

    base = settings.avatar_public_base_url.rstrip("/")
    public_url = f"{base}/{current_user.user_id}/{filename}"

    current_user.avatar_url = public_url
    await session.commit()
    await session.refresh(current_user)
    logger.info(
        f"me.avatar.upload.ok user_id={current_user.user_id} "
        f"size={total}B mime={content_type}"
    )
    return UserPublic.model_validate(current_user)
