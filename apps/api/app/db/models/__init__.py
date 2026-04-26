"""ORM 模型注册中心.

⚠️ 任何新增 model 必须在此 import, 否则 ``Base.metadata`` 不会发现它,
   alembic autogenerate 会漏掉新表。
"""

from app.db.models.auth import AuthSession
from app.db.models.invite import InviteCode
from app.db.models.ipo import IPO, IPODocument
from app.db.models.push import PushToken
from app.db.models.user import User, UserFavorite

__all__ = [
    "AuthSession",
    "InviteCode",
    "IPO",
    "IPODocument",
    "PushToken",
    "User",
    "UserFavorite",
]
