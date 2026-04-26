from fastapi import APIRouter

from app.api.v1 import agent, auth, ipos

router = APIRouter(prefix="/api/v1")
router.include_router(ipos.router)
router.include_router(agent.router)
router.include_router(auth.router)

__all__ = ["router"]
