"""``GET /api/v1/users/{user_id}/public`` 公开资料 (BUG-S6.8-003).

Sprint 6.8 用户上报 bug ③: 社区帖子点击作者昵称应跳到一个"个人公开页", 显示
该用户头像 / 昵称 / 注册时间 / 帖子数 (用户决策 ``minimal``).

为什么单独建路由不复用 ``/me``
==============================
- ``/me`` 走 ``get_current_user`` 强鉴权 + 返自己的全字段 (含 invite_code 等私有)
- 公开页是查**他人**, 鉴权可选 (匿名也能看), 字段必须脱敏 (绝不返 phone /
  wechat / region / invite_code 等任何 PII)

字段集 (用户决策 minimal)
=========================
- ``user_id``: 用户唯一标识 (用于 FE store key + URL 参数回显)
- ``nickname``: 显示名 (空则前端 fallback "匿名用户")
- ``avatar_url``: 头像 URL (空则前端用昵称首字)
- ``created_at``: 注册时间 (UI 显示 ``"2026-04-15 加入"``, 不暴露具体小时)
- ``posts_count``: 该用户已发布帖子数 (仅 ``status='published'`` 计入,
  rejected / pending / deleted 不暴露给陌生人, 防隐私泄漏)

为什么不返 region
==================
PIPL 偏严: ``region`` 字段虽不构成 PII (只到省级 / 国家), 但社区是匿名场景,
显示"广东用户"会让陌生人联想到"地理画像"; 默认不暴露, 后续如果要做"同省发现"
等社交功能再加 ``visibility`` 字段控制可见性。

为什么不返邮箱 / 手机
=====================
PIPL §47: 暴露 PII 必须有用户授权, 当前默认零暴露; 真要做"加好友"需要先做
""授权征询"" 流程 (Sprint 7+).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db.models import User
from app.db.models.community import CommunityPost

router = APIRouter(prefix="/users", tags=["users"])


class UserPublicProfile(BaseModel):
    """公开资料 (脱敏过, 任何人能看; 仅供社区展示).

    与 :class:`app.schemas.auth.UserPublic` 区别:
    - 不返 ``invite_code`` / ``status`` / ``region`` (PII / 内部字段)
    - 加 ``posts_count`` (社区上下文专用)
    """

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    nickname: str | None = Field(default=None, description="显示名; 空则 FE fallback")
    avatar_url: str | None = Field(default=None, description="头像 URL")
    created_at: str = Field(description="注册时间 ISO-8601 (UTC)")
    posts_count: int = Field(default=0, ge=0, description="已发布帖子数")


@router.get(
    "/{user_id}/public",
    response_model=UserPublicProfile,
    status_code=status.HTTP_200_OK,
    summary="他人公开资料 (脱敏; 任何人可见, 含匿名访问)",
    responses={
        404: {"description": "用户不存在 / 已注销"},
    },
)
async def get_user_public_profile(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> UserPublicProfile:
    """公开资料查询 — 不需鉴权 (默认任何人可见).

    Soft-deleted 用户 (``deleted_at IS NOT NULL``) 一律 404, 与社区帖子
    "墓碑化"策略一致 (帖子按 ``status`` 软删 → 计数也降, posts_count 自然反映).
    """
    user_row = (
        await session.execute(
            select(User).where(
                User.user_id == user_id,
                User.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if user_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "user_not_found", "message": "用户不存在或已注销"},
        )

    posts_count = (
        await session.execute(
            select(func.count())
            .select_from(CommunityPost)
            .where(
                CommunityPost.user_id == user_id,
                CommunityPost.status == "published",
            )
        )
    ).scalar_one()

    return UserPublicProfile(
        user_id=user_row.user_id,
        nickname=user_row.nickname,
        avatar_url=user_row.avatar_url,
        created_at=user_row.created_at.isoformat(),
        posts_count=int(posts_count),
    )
