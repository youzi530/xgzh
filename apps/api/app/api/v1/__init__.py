from fastapi import APIRouter

from app.api.v1 import (
    admin,
    agent,
    articles,
    auth,
    brokers,
    chat,
    community,
    favorites,
    feature_flags,
    feedback,
    invite,
    ipos,
    knowledge,
    me,
    payment,
    push,
    subscriptions,
    vip,
)

router = APIRouter(prefix="/api/v1")
router.include_router(ipos.router)
router.include_router(agent.router)
router.include_router(chat.router)
router.include_router(auth.router)
router.include_router(me.router)
router.include_router(invite.router)
router.include_router(favorites.router)
router.include_router(push.router)
router.include_router(articles.router)
router.include_router(articles.search_router)
router.include_router(brokers.router)
router.include_router(vip.router)
router.include_router(payment.router)
router.include_router(feedback.router)
router.include_router(subscriptions.router)
router.include_router(knowledge.router)
router.include_router(community.router)
router.include_router(feature_flags.router)
router.include_router(admin.router)

__all__ = ["router"]
