"""Top-level API router."""

from fastapi import APIRouter

from app.api.routes.agent_runs import router as agent_runs_router
from app.api.routes.goals import router as goals_router
from app.api.routes.health import router as health_router
from app.api.routes.plans import router as plans_router
from app.api.routes.reviews import router as reviews_router
from app.api.routes.study_sessions import router as study_sessions_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["system"])
api_router.include_router(agent_runs_router, tags=["agent runs"])
api_router.include_router(goals_router, tags=["goals"])
api_router.include_router(plans_router, tags=["plans"])
api_router.include_router(study_sessions_router, tags=["study sessions"])
api_router.include_router(reviews_router, tags=["reviews"])
