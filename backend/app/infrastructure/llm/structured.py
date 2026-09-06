"""Pydantic-first structured generation with one bounded repair attempt."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agent.prompts import PromptTemplate
from app.infrastructure.llm.errors import StructuredOutputError
from app.infrastructure.llm.models import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    TokenUsage,
)


@dataclass(frozen=True, slots=True)
class StructuredResult[OutputT: BaseModel]:
    value: OutputT
    usage: TokenUsage
    model: str
    prompt_name: str
    prompt_version: str
    repaired: bool


class StructuredModel:
    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    async def generate[OutputT: BaseModel](
        self,
        prompt: PromptTemplate,
        values: BaseModel | Mapping[str, Any],
        output_type: type[OutputT],
        *,
        max_output_tokens: int = 4_096,
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
                    f"JSON Schema：{schema_json}"
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
