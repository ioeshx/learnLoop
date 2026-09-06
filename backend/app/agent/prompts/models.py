"""Versioned and typed prompt definitions."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict


class PromptInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    system: str
    user: str


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str
    version: str
    use_case: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    system_template: str
    user_template: str
    test_input: Mapping[str, Any]

    def render(self, values: BaseModel | Mapping[str, Any]) -> RenderedPrompt:
        validated = self.input_schema.model_validate(values)
        serialized = {
            key: _stringify(value)
            for key, value in validated.model_dump(mode="json").items()
        }
        return RenderedPrompt(
            system=self.system_template.strip(),
            user=self.user_template.format_map(serialized).strip(),
        )


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
