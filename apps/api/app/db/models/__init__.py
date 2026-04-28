"""ORM 模型注册中心.

⚠️ 任何新增 model 必须在此 import, 否则 ``Base.metadata`` 不会发现它,
   alembic autogenerate 会漏掉新表。
"""

from app.db.models.article import Article, ArticleTopic
from app.db.models.auth import AuthSession
from app.db.models.broker import Broker, ConversionEvent
from app.db.models.chat import (
    ChatMessage,
    ChatSession,
    ChatTokenUsage,
    ChatToolCall,
)
from app.db.models.feedback import Feedback
from app.db.models.invite import InviteCode
from app.db.models.ipo import IPO, IPODocument
from app.db.models.push import PushToken
from app.db.models.user import User, UserFavorite
from app.db.models.vip import VipMembership, VipOrder

__all__ = [
    "Article",
    "ArticleTopic",
    "AuthSession",
    "Broker",
    "ChatMessage",
    "ChatSession",
    "ChatTokenUsage",
    "ChatToolCall",
    "ConversionEvent",
    "Feedback",
    "InviteCode",
    "IPO",
    "IPODocument",
    "PushToken",
    "User",
    "UserFavorite",
    "VipMembership",
    "VipOrder",
]
