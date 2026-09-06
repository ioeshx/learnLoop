"""Prompt catalog metadata and rendering tests."""

from app.agent.prompts import PROMPT_CATALOG


def test_prompt_catalog_has_unique_versioned_contracts() -> None:
    identities = {(prompt.name, prompt.version) for prompt in PROMPT_CATALOG}

    assert len(PROMPT_CATALOG) == 6
    assert len(identities) == len(PROMPT_CATALOG)
    assert all(prompt.use_case for prompt in PROMPT_CATALOG)
    assert all(prompt.input_schema for prompt in PROMPT_CATALOG)
    assert all(prompt.output_schema for prompt in PROMPT_CATALOG)


def test_every_prompt_renders_its_test_sample() -> None:
    for prompt in PROMPT_CATALOG:
        rendered = prompt.render(prompt.test_input)

        assert rendered.system
        assert rendered.user
        assert "{" not in rendered.user or "}" in rendered.user
