"""FastAPI dependencies for application services."""

from typing import Annotated, cast

from fastapi import Depends, Request

from app.agent.execution import AgentRuntime
from app.application.services import ApplicationDependencies
from app.infrastructure.rag import RagService
from app.workers import JobService


async def get_application_dependencies(request: Request) -> ApplicationDependencies:
    return cast(ApplicationDependencies, request.app.state.application_dependencies)


ApplicationDependenciesDep = Annotated[
    ApplicationDependencies, Depends(get_application_dependencies)
]


async def get_agent_runtime(request: Request) -> AgentRuntime:
    return cast(AgentRuntime, request.app.state.agent_runtime)


AgentRuntimeDep = Annotated[AgentRuntime, Depends(get_agent_runtime)]


async def get_rag_service(request: Request) -> RagService:
    return cast(RagService, request.app.state.rag_service)


RagServiceDep = Annotated[RagService, Depends(get_rag_service)]


async def get_job_service(request: Request) -> JobService:
    """从 FastAPI 生命周期状态中取得共享的后台任务应用服务。"""

    return cast(JobService, request.app.state.job_service)


JobServiceDep = Annotated[JobService, Depends(get_job_service)]
