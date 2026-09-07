"""Local learning-resource ingestion and hybrid-search endpoints."""

from typing import Annotated

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response

from app.api.dependencies import JobServiceDep, RagServiceDep
from app.api.schemas import (
    BackgroundJobResponse,
    CitationResponse,
    IngestUrlRequest,
    ResourceImportResponse,
    ResourceResponse,
)

router = APIRouter(prefix="/resources")


@router.post("/files", response_model=ResourceImportResponse, status_code=202)
async def upload_resource_file(
    service: RagServiceDep,
    jobs: JobServiceDep,
    file: Annotated[UploadFile, File()],
    goal_id: Annotated[str, Form(min_length=1)],
    knowledge_node_id: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form(max_length=500)] = None,
) -> ResourceImportResponse:
    try:
        resource = await service.prepare_file(
            file.file,
            filename=file.filename or "document.txt",
            media_type=file.content_type or "application/octet-stream",
            goal_id=goal_id,
            knowledge_node_id=knowledge_node_id,
            title=title,
        )
        job = await jobs.enqueue_resource(resource.id)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="embedding service request failed",
        ) from error
    finally:
        await file.close()
    return ResourceImportResponse(
        resource=ResourceResponse.from_domain(resource),
        job=BackgroundJobResponse.from_domain(job),
    )


@router.post("/url", response_model=ResourceImportResponse, status_code=202)
async def import_resource_url(
    payload: IngestUrlRequest, service: RagServiceDep, jobs: JobServiceDep
) -> ResourceImportResponse:
    try:
        resource = await service.prepare_url(
            payload.url,
            goal_id=payload.goal_id,
            knowledge_node_id=payload.knowledge_node_id,
            title=payload.title,
        )
        job = await jobs.enqueue_resource(resource.id)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="failed to fetch the web resource",
        ) from error
    return ResourceImportResponse(
        resource=ResourceResponse.from_domain(resource),
        job=BackgroundJobResponse.from_domain(job),
    )


@router.get("", response_model=list[ResourceResponse])
async def list_resources(
    service: RagServiceDep,
    goal_id: str | None = None,
    knowledge_node_id: str | None = None,
) -> list[ResourceResponse]:
    resources = await service.list_resources(
        goal_id=goal_id, knowledge_node_id=knowledge_node_id
    )
    return [ResourceResponse.from_domain(resource) for resource in resources]


@router.get("/search", response_model=list[CitationResponse])
async def search_resources(
    service: RagServiceDep,
    query: Annotated[str, Query(min_length=1, max_length=2_000)],
    goal_id: Annotated[str, Query(min_length=1)],
    knowledge_node_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> list[CitationResponse]:
    try:
        citations = await service.search(
            query,
            goal_id=goal_id,
            knowledge_node_id=knowledge_node_id,
            limit=limit,
        )
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="embedding service request failed",
        ) from error
    return [CitationResponse.from_domain(citation) for citation in citations]


@router.get("/{resource_id}", response_model=ResourceResponse)
async def get_resource(
    resource_id: str, service: RagServiceDep
) -> ResourceResponse:
    return ResourceResponse.from_domain(await service.get(resource_id))


@router.delete("/{resource_id}", status_code=204)
async def delete_resource(
    resource_id: str, service: RagServiceDep, jobs: JobServiceDep
) -> Response:
    await jobs.cancel_resource_jobs(resource_id)
    await service.delete(resource_id)
    return Response(status_code=204)
