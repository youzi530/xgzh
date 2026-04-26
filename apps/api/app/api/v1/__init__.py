from fastapi import APIRouter

from app.api.v1 import agent, auth, ipos, me

router = APIRouter(prefix="/api/v1")
router.include_router(ipos.router)
router.include_router(agent.router)
router.include_router(auth.router)
router.include_router(me.router)

__all__ = ["router"]
