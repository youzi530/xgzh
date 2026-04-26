from fastapi import APIRouter

from app.api.v1 import agent, auth, favorites, invite, ipos, me

router = APIRouter(prefix="/api/v1")
router.include_router(ipos.router)
router.include_router(agent.router)
router.include_router(auth.router)
router.include_router(me.router)
router.include_router(invite.router)
router.include_router(favorites.router)

__all__ = ["router"]
