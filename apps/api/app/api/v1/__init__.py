from fastapi import APIRouter

from app.api.v1 import agent, auth, chat, favorites, invite, ipos, me, push

router = APIRouter(prefix="/api/v1")
router.include_router(ipos.router)
router.include_router(agent.router)
router.include_router(chat.router)
router.include_router(auth.router)
router.include_router(me.router)
router.include_router(invite.router)
router.include_router(favorites.router)
router.include_router(push.router)

__all__ = ["router"]
