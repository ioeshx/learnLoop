"""General, policy-bounded Agent Team runtime."""

from app.agent.team.adapters import EvaluatorRoleAdapter, ResearcherRoleAdapter
from app.agent.team.models import (
    AgentCard,
    ArtifactDraft,
    TeamArtifact,
    TeamBudget,
    TeamFailurePolicy,
    TeamPart,
    TeamRunRequest,
    TeamRunResult,
    TeamTask,
    TeamTaskSpec,
    TeamTaskStatus,
)
from app.agent.team.registry import RoleAdapter, RoleRegistry
from app.agent.team.service import AgentTeamService
from app.agent.team.verifier import TeamArtifactVerifier

__all__ = [
    "AgentCard",
    "AgentTeamService",
    "ArtifactDraft",
    "EvaluatorRoleAdapter",
    "ResearcherRoleAdapter",
    "RoleAdapter",
    "RoleRegistry",
    "TeamArtifact",
    "TeamArtifactVerifier",
    "TeamBudget",
    "TeamFailurePolicy",
    "TeamPart",
    "TeamRunRequest",
    "TeamRunResult",
    "TeamTask",
    "TeamTaskSpec",
    "TeamTaskStatus",
]
