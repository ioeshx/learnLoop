"""Durable Agent run, resume, and replayable SSE endpoints."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from app.agent.execution import AgentEvent, AgentRun, AgentRuntime
from app.api.dependencies import AgentRuntimeDep
from app.api.schemas import (
    AgentEventResponse,
    AgentRunResponse,
    AgentTraceResponse,
    ModelCallTraceResponse,
    ResumeAgentRunRequest,
    ToolCallTraceResponse,
)

router = APIRouter(prefix="/agent")


@router.post("/study-sessions/{session_id}/runs")
async def start_daily_learning_run(
    session_id: str, runtime: AgentRuntimeDep
) -> StreamingResponse:
    run, created = await runtime.create_run("daily_learning", session_id)
    return await _run_stream_response(runtime, run, created=created)


@router.post("/goals/{goal_id}/runs")
async def start_goal_planning_run(
    goal_id: str, runtime: AgentRuntimeDep
) -> StreamingResponse:
    if runtime.model is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="goal planning requires an enabled LLM provider",
        )
    run, created = await runtime.create_run("goal_planning", goal_id)
    return await _run_stream_response(runtime, run, created=created)


@router.post("/runs/{run_id}/resume")
async def resume_agent_run(
    run_id: str,
    payload: ResumeAgentRunRequest,
    runtime: AgentRuntimeDep,
) -> StreamingResponse:
    run = await _get_run(runtime, run_id)
    if run.status != "awaiting_input":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"agent run is '{run.status}', not awaiting input",
        )
    existing = await runtime.run_store.list_events(run.run_id)
    after_sequence = existing[-1].sequence if existing else 0
    await runtime.start_execution(run, resume=payload.value)
    return StreamingResponse(
        _replay_events(runtime, run, after_sequence),
        media_type="text/event-stream",
        headers=_stream_headers(run),
    )


@router.get("/runs", response_model=list[AgentRunResponse])
async def list_agent_runs(
    runtime: AgentRuntimeDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AgentRunResponse]:
    runs = await runtime.run_store.list_runs(limit=limit)
    return [AgentRunResponse.from_execution(run) for run in runs]


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    run_id: str, runtime: AgentRuntimeDep
) -> AgentRunResponse:
    return AgentRunResponse.from_execution(await _get_run(runtime, run_id))


@router.get("/runs/{run_id}/state")
async def get_agent_run_state(
    run_id: str, runtime: AgentRuntimeDep
) -> dict[str, object]:
    run = await _get_run(runtime, run_id)
    return await runtime.get_checkpoint_state(run)


@router.get("/runs/{run_id}/trace", response_model=AgentTraceResponse)
async def get_agent_trace(
    run_id: str, runtime: AgentRuntimeDep
) -> AgentTraceResponse:
    run = await _get_run(runtime, run_id)
    events = await runtime.run_store.list_events(run.run_id)
    tool_calls = await runtime.run_store.list_tool_calls(run.run_id)
    model_calls = await runtime.run_store.list_model_calls(run.run_id)
    return AgentTraceResponse(
        run=AgentRunResponse.from_execution(run),
        events=[AgentEventResponse.from_execution(event) for event in events],
        tool_calls=[
            ToolCallTraceResponse.from_execution(call) for call in tool_calls
        ],
        model_calls=[
            ModelCallTraceResponse.from_execution(call) for call in model_calls
        ],
        total_tokens=sum(call.total_tokens for call in model_calls),
        total_model_duration_ms=sum(call.duration_ms for call in model_calls),
        total_tool_duration_ms=sum(call.duration_ms or 0.0 for call in tool_calls),
    )


@router.get("/prompts")
async def list_prompt_versions(
    runtime: AgentRuntimeDep,
) -> list[dict[str, str]]:
    return await runtime.run_store.list_prompt_versions()


@router.get("/runs/{run_id}/events")
async def replay_agent_events(
    run_id: str,
    runtime: AgentRuntimeDep,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    run = await _get_run(runtime, run_id)
    cursor = max(after, _parse_last_event_id(last_event_id))
    return StreamingResponse(
        _replay_events(runtime, run, cursor),
        media_type="text/event-stream",
        headers=_stream_headers(run),
    )


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_run(
    run_id: str, runtime: AgentRuntimeDep
) -> Response:
    run = await _get_run(runtime, run_id)
    await runtime.delete_run(run)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _run_stream_response(
    runtime: AgentRuntime, run: AgentRun, *, created: bool
) -> StreamingResponse:
    if created:
        await runtime.start_execution(run)
        return StreamingResponse(
            _replay_events(runtime, run, 0),
            media_type="text/event-stream",
            headers=_stream_headers(run),
        )
    return StreamingResponse(
        _stored_events(runtime, run),
        media_type="text/event-stream",
        headers=_stream_headers(run),
    )

async def _stored_events(
    runtime: AgentRuntime, run: AgentRun
) -> AsyncIterator[str]:
    for event in await runtime.run_store.list_events(run.run_id):
        yield _encode_sse(event)


async def _replay_events(
    runtime: AgentRuntime, run: AgentRun, after_sequence: int
) -> AsyncIterator[str]:
    cursor = after_sequence
    while True:
        events = await runtime.run_store.list_events(
            run.run_id, after_sequence=cursor
        )
        for event in events:
            cursor = event.sequence
            yield _encode_sse(event)
        refreshed = await runtime.run_store.get(run.run_id)
        if refreshed is None or refreshed.status in {
            "awaiting_input",
            "completed",
            "failed",
        }:
            return
        yield ": keep-alive\n\n"
        await asyncio.sleep(1)


def _encode_sse(event: AgentEvent) -> str:
    payload = json.dumps(
        event.as_dict(), ensure_ascii=False, separators=(",", ":")
    )
    return f"id: {event.sequence}\nevent: {event.event}\ndata: {payload}\n\n"


def _stream_headers(run: AgentRun) -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "X-Agent-Run-Id": run.run_id,
        "X-Agent-Thread-Id": run.thread_id,
    }


async def _get_run(runtime: AgentRuntime, run_id: str) -> AgentRun:
    run = await runtime.run_store.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="agent run was not found",
        )
    return run


def _parse_last_event_id(value: str | None) -> int:
    if value is None:
        return 0
    try:
        parsed = int(value)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Last-Event-ID must be an integer",
        ) from error
    if parsed < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Last-Event-ID cannot be negative",
        )
    return parsed
