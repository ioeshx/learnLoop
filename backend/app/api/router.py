"""Top-level API router."""

from fastapi import APIRouter

from app.api.routes.agent_runs import router as agent_runs_router
from app.api.routes.agent_team import router as agent_team_router
from app.api.routes.data import router as data_router
from app.api.routes.goals import router as goals_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.memories import router as memories_router
from app.api.routes.model_gateway import router as model_gateway_router
from app.api.routes.optimization import router as optimization_router
from app.api.routes.plans import router as plans_router
from app.api.routes.policies import router as policies_router
from app.api.routes.reliability import router as reliability_router
from app.api.routes.research import router as research_router
from app.api.routes.resources import router as resources_router
from app.api.routes.reviews import router as reviews_router
from app.api.routes.study_sessions import router as study_sessions_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["system"])
api_router.include_router(jobs_router, tags=["background jobs"])
api_router.include_router(agent_runs_router, tags=["agent runs"])
api_router.include_router(agent_team_router, tags=["agent team"])
api_router.include_router(optimization_router, tags=["agent optimization"])
api_router.include_router(policies_router, tags=["agent policy"])
api_router.include_router(model_gateway_router, tags=["model gateway"])
api_router.include_router(memories_router, tags=["agent memory"])
api_router.include_router(data_router, tags=["data"])
api_router.include_router(goals_router, tags=["goals"])
api_router.include_router(plans_router, tags=["plans"])
api_router.include_router(study_sessions_router, tags=["study sessions"])
api_router.include_router(reviews_router, tags=["reviews"])
api_router.include_router(resources_router, tags=["resources"])
api_router.include_router(research_router, tags=["research tutor"])
api_router.include_router(reliability_router, tags=["agent reliability"])
