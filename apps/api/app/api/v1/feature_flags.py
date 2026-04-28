"""OPS-S4-001 公开 feature-flag 评估端点.

与 ``admin.py`` 区别:
- ``admin/flags`` 是配置面 (写 / 读全量配置), 走 ``X-Admin-Token`` 鉴权
- ``feature-flags`` 是评估面 (前端"我能看到这个 flag 吗"), 公开 + 可选鉴权;
  匿名走 ``rollout_pct >= 50`` 规则, 登录用户走稳定 hash bucket

接口: ``GET /api/v1/feature-flags?names=history_tab,ai_report``
返回: ``{"flags": {"history_tab": true, "ai_report": false}, "user_id": "uuid|null"}``

设计要点:
- 走 ``get_optional_user`` 让匿名也能查 (匿名拿不到登录用户特性, 但拿"已全开"特性);
  登录用户拿到他自己的稳定 bucket 结果, 跟 BE 服务端做权限决策时一致 (灰度可信)
- ``names`` 上限 20 个 (防恶意 query 长度); 超过的丢弃 + warning
- 返回 ``user_id`` (匿名 None) 让 FE 缓存 key 知道是 anon 还是某用户的命中结果
- 不缓存响应: 灰度旋钮变化要实时生效; 1 次 redis hgetall ~毫秒级, 可承受
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel

from app.db.models import User
from app.security.deps import get_optional_user
from app.services import feature_flags

router = APIRouter(tags=["feature-flags"])

MAX_NAMES_PER_REQUEST = 20


class FeatureFlagsEvalResponse(BaseModel):
    flags: dict[str, bool]
    user_id: str | None


@router.get("/feature-flags", response_model=FeatureFlagsEvalResponse)
async def evaluate_flags(
    names: str = Query(
        ...,
        description="逗号分隔的 flag 名列表, 上限 20",
        examples=["history_tab,ai_report"],
    ),
    user: User | None = Depends(get_optional_user),
) -> FeatureFlagsEvalResponse:
    raw_names = [n.strip() for n in names.split(",") if n.strip()]
    if len(raw_names) > MAX_NAMES_PER_REQUEST:
        logger.warning(
            f"feature_flags.eval.too_many names_count={len(raw_names)} truncated_to={MAX_NAMES_PER_REQUEST}"
        )
        raw_names = raw_names[:MAX_NAMES_PER_REQUEST]

    user_id_str = str(user.user_id) if user is not None else None
    flags: dict[str, bool] = {}
    for name in raw_names:
        flags[name] = await feature_flags.is_enabled(name, user_id=user_id_str)

    return FeatureFlagsEvalResponse(flags=flags, user_id=user_id_str)


__all__ = ["router"]
