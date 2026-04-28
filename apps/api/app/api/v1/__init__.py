from fastapi import APIRouter

from app.api.v1 import (
    agent,
    articles,
    auth,
    brokers,
    chat,
    favorites,
    invite,
    ipos,
    me,
    payment,
    push,
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

__all__ = ["router"]
