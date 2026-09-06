"""Custom stream events around thin Tool calls."""

from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.runtime import Runtime


async def call_tool[ResultT](
    runtime: Runtime[Any], name: str, operation: Awaitable[ResultT]
) -> ResultT:
    runtime.stream_writer({"event": "tool_started", "tool": name, "data": {}})
    result = await operation
    runtime.stream_writer({"event": "tool_completed", "tool": name, "data": {}})
    return result


def call_sync_tool[ResultT](
    runtime: Runtime[Any], name: str, operation: Callable[[], ResultT]
) -> ResultT:
    runtime.stream_writer({"event": "tool_started", "tool": name, "data": {}})
    result = operation()
    runtime.stream_writer({"event": "tool_completed", "tool": name, "data": {}})
    return result
