"""通用列 mixin: 给业务表统一加 created_at / updated_at / deleted_at.

按 ``.cursor/rules/40-database.mdc`` 规定:
- 时间戳一律 ``TIMESTAMPTZ``, 命名以 ``_at`` 结尾
- 软删除场景额外加 ``deleted_at TIMESTAMPTZ``

写成 mixin 而非 Base 默认列, 是为了让 ``ipo_documents`` 之类
高吞吐 RAG chunk 表可选不带 deleted_at, 减少索引维护成本。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """业务表统一带 created_at / updated_at."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """启用软删除. 查询统一附加 ``WHERE deleted_at IS NULL``."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
