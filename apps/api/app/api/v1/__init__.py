from fastapi import APIRouter

from app.api.v1 import agent, ipos

router = APIRouter(prefix="/api/v1")
router.include_router(ipos.router)
router.include_router(agent.router)

__all__ = ["router"]
