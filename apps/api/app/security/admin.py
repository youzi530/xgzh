"""OPS-S4-001 Admin 路由保护依赖.

为什么不复用 ``get_current_user`` + ``users.is_admin``?
1. 现有 ``users`` 表无 ``is_admin`` / ``role`` 列, 加 schema 改动太大且非 Sprint 4 范围.
2. Admin API 量级很小 (灰度旋钮 / 错误率查询 / Bad Case burndown), 走 token-only
   "ops 工具人凭票入场"模式更轻; 后续真要做用户级 RBAC 再迁。
3. 配合 ``settings.ops_admin_token`` 双重护栏: 留空 = 路由直接 503 (拒绝任何写),
   生产环境必须显式配置 32+ 字节随机串. 用 ``X-Admin-Token`` header.

设计要点:
- 401 vs 503: token 不匹配 → 401; token 未配置 (server-side) → 503 service_unavailable
  让运维一眼分清楚是"我没拿对 token"还是"线上还没接 admin"
- 用 ``hmac.compare_digest`` 防时序侧信道 (即使 token 长度差异, 也要等长比对才安全)
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from app.core.config import get_settings


async def require_admin_token(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """admin 路由必加依赖. 缺 / 错 / server 侧未配置 都拒。"""
    settings = get_settings()
    expected = settings.ops_admin_token.strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "admin_disabled",
                "message": "Admin endpoints 未启用 (设置 OPS_ADMIN_TOKEN 后生效)",
            },
        )
    given = (x_admin_token or "").strip()
    if not given or not hmac.compare_digest(expected, given):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "admin_token_invalid",
                "message": "Admin token 缺失或不匹配",
            },
        )


__all__ = ["require_admin_token"]
