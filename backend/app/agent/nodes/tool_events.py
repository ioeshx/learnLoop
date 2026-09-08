"""Custom stream events around thin Tool calls."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from langgraph.runtime import Runtime


async def call_tool[ResultT](
    runtime: Runtime[Any],
    name: str,
    operation: Awaitable[ResultT],
    *,
    arguments: dict[str, object] | None = None,
) -> ResultT:
    call_id = str(uuid4())
    started_at = datetime.now(UTC)
    started = perf_counter()
    runtime.stream_writer(
        {
            "event": "tool_started",
            "tool": name,
            "data": {
                "call_id": call_id,
                "arguments": arguments or {},
                "started_at": started_at.isoformat(),
            },
        }
    )
    try:
        result = await operation
    except Exception as error:
        runtime.stream_writer(
            {
                "event": "tool_completed",
                "tool": name,
                "data": {
                    "call_id": call_id,
                    "status": "failed",
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                    "result_summary": {},
                    "error": f"{type(error).__name__}: {str(error)[:500]}",
                    "completed_at": datetime.now(UTC).isoformat(),
                },
            }
        )
        raise
    runtime.stream_writer(
        {
            "event": "tool_completed",
            "tool": name,
            "data": {
                "call_id": call_id,
                "status": "succeeded",
                "duration_ms": round((perf_counter() - started) * 1000, 3),
                "result_summary": _summarize_result(result),
                "error": None,
                "completed_at": datetime.now(UTC).isoformat(),
            },
        }
    )
    return result


def call_sync_tool[ResultT](
    runtime: Runtime[Any],
    name: str,
    operation: Callable[[], ResultT],
    *,
    arguments: dict[str, object] | None = None,
) -> ResultT:
    call_id = str(uuid4())
    started_at = datetime.now(UTC)
    started = perf_counter()
    runtime.stream_writer(
        {
            "event": "tool_started",
            "tool": name,
            "data": {
                "call_id": call_id,
                "arguments": arguments or {},
                "started_at": started_at.isoformat(),
            },
        }
    )
    try:
        result = operation()
    except Exception as error:
        runtime.stream_writer(
            {
                "event": "tool_completed",
                "tool": name,
                "data": {
                    "call_id": call_id,
                    "status": "failed",
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                    "result_summary": {},
                    "error": f"{type(error).__name__}: {str(error)[:500]}",
                    "completed_at": datetime.now(UTC).isoformat(),
                },
            }
        )
        raise
    runtime.stream_writer(
        {
            "event": "tool_completed",
            "tool": name,
            "data": {
                "call_id": call_id,
                "status": "succeeded",
                "duration_ms": round((perf_counter() - started) * 1000, 3),
                "result_summary": _summarize_result(result),
                "error": None,
                "completed_at": datetime.now(UTC).isoformat(),
            },
        }
    )
    return result


def _summarize_result(result: object) -> dict[str, object]:
    if isinstance(result, dict):
        return {"type": "object", "keys": sorted(str(key) for key in result)}
    if isinstance(result, (list, tuple, set)):
        return {"type": "collection", "count": len(result)}
    return {"type": type(result).__name__}
