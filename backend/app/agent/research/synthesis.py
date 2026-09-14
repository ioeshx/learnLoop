"""Model-backed Claim synthesis behind a strict structured boundary."""

from app.agent.prompts.research import RESEARCH_SYNTHESIS_PROMPT
from app.agent.research.models import (
    ClaimDraft,
    ClaimDraftSet,
    EvidenceItem,
    ResearchRequest,
    SubQuestion,
)
from app.infrastructure.llm import StructuredModel


class ModelResearchSynthesizer:
    """Use an LLM only for atomic Claim proposals, never for trust decisions."""

    def __init__(self, model: StructuredModel) -> None:
        self.model = model

    async def propose_claims(
        self,
        request: ResearchRequest,
        subquestions: list[SubQuestion],
        evidence: list[EvidenceItem],
    ) -> list[ClaimDraft]:
        result = await self.model.generate(
            RESEARCH_SYNTHESIS_PROMPT,
            {
                "question": request.question,
                "subquestions": [
                    item.model_dump(mode="json") for item in subquestions
                ],
                "evidence": [
                    {
                        "id": item.id,
                        "excerpt": item.excerpt,
                        "resource_id": item.resource_id,
                        "chunk_id": item.chunk_id,
                        "title": item.title,
                    }
                    for item in evidence
                ],
            },
            ClaimDraftSet,
            max_output_tokens=2_048,
        )
        return result.value.claims
