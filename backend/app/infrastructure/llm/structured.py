"""Pydantic-first structured generation with one bounded repair attempt."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agent.prompts import PromptTemplate
from app.infrastructure.llm.errors import StructuredOutputError
from app.infrastructure.llm.models import (
    ModelCallObservation,
    ModelCallObserver,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    TokenUsage,
)
from app.observability import current_agent_run_id


@dataclass(frozen=True, slots=True)
class StructuredResult[OutputT: BaseModel]:
    value: OutputT
    usage: TokenUsage
    model: str
    prompt_name: str
    prompt_version: str
    repaired: bool


class StructuredModel:
    def __init__(
        self, provider: ModelProvider, observer: ModelCallObserver | None = None
    ) -> None:
        self.provider = provider
        self._observer = observer

    def set_observer(self, observer: ModelCallObserver | None) -> None:
        self._observer = observer

    async def generate[OutputT: BaseModel](
        self,
        prompt: PromptTemplate,
        values: BaseModel | Mapping[str, Any],
        output_type: type[OutputT],
        *,
        max_output_tokens: int = 4_096,
    ) -> StructuredResult[OutputT]:
        started = perf_counter()
        try:
            result = await self._generate_validated(
                prompt, values, output_type, max_output_tokens=max_output_tokens
            )
        except Exception as error:
            if self._observer is not None:
                attempts = (
                    error.attempts
                    if isinstance(error, StructuredOutputError)
                    else 1
                )
                await self._observer(
                    ModelCallObservation(
                        run_id=current_agent_run_id(),
                        prompt_name=prompt.name,
                        prompt_version=prompt.version,
                        model=self.provider.name,
                        usage=TokenUsage(),
                        duration_ms=(perf_counter() - started) * 1000,
                        attempts=attempts,
                        repaired=False,
                        error=f"{type(error).__name__}: {str(error)[:500]}",
                    )
                )
            raise
        if self._observer is not None:
            await self._observer(
                ModelCallObservation(
                    run_id=current_agent_run_id(),
                    prompt_name=result.prompt_name,
                    prompt_version=result.prompt_version,
                    model=result.model,
                    usage=result.usage,
                    duration_ms=(perf_counter() - started) * 1000,
                    attempts=2 if result.repaired else 1,
                    repaired=result.repaired,
                )
            )
        return result

    async def _generate_validated[OutputT: BaseModel](
        self,
        prompt: PromptTemplate,
        values: BaseModel | Mapping[str, Any],
        output_type: type[OutputT],
        *,
        max_output_tokens: int,
    ) -> StructuredResult[OutputT]:
        if prompt.output_schema is not output_type:
            raise ValueError("output_type must match the prompt output schema")

        rendered = prompt.render(values)
        schema_json = json.dumps(
            output_type.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )
        messages = (
            ModelMessage(
                role="system",
                content=(
                    f"{rendered.system}\n\n"
                    "输出必须是一个 JSON 对象，不要使用 Markdown 代码块。"
                    f"JSON Schema:{schema_json}"
                ),
            ),
            ModelMessage(role="user", content=rendered.user),
        )
        first = await self.provider.complete(
            ModelRequest(
                prompt_name=prompt.name,
                prompt_version=prompt.version,
                messages=messages,
                max_output_tokens=max_output_tokens,
            )
        )
        try:
            value = _validate(first.content, output_type)
        except (json.JSONDecodeError, ValidationError) as first_error:
            repair = await self.provider.complete(
                ModelRequest(
                    prompt_name=prompt.name,
                    prompt_version=prompt.version,
                    messages=(
                        *messages,
                        # hard limit(8000) to avoid token explosion
                        ModelMessage(role="assistant", content=first.content[:8_000]),
                        ModelMessage(
                            role="user",
                            content=(
                                "上一个输出未通过校验。只修复格式或字段，不改变原任务。"
                                "返回一个完整 JSON 对象。校验错误："
                                f"{str(first_error)[:2_000]}"
                            ),
                        ),
                    ),
                    max_output_tokens=max_output_tokens,
                    is_repair=True,
                )
            )
            try:
                value = _validate(repair.content, output_type)
            except (json.JSONDecodeError, ValidationError) as repair_error:
                raise StructuredOutputError(
                    prompt_name=prompt.name,
                    attempts=2,
                    reason=str(repair_error)[:1_000],
                ) from repair_error
            return StructuredResult(
                value=value,
                usage=first.usage + repair.usage,
                model=repair.model,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
                repaired=True,
            )

        return StructuredResult(
            value=value,
            usage=first.usage,
            model=first.model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            repaired=False,
        )


def _validate[OutputT: BaseModel](content: str, output_type: type[OutputT]) -> OutputT:
    decoded = json.loads(content)
    return output_type.model_validate(decoded)
