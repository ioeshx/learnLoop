"""Lead-side integrity and information-flow verifier for Team Artifacts."""

from __future__ import annotations

import hashlib
import json

from app.agent.policy import DataSource, join_labels
from app.agent.team.models import AgentCard, ArtifactDraft, TeamArtifact, TeamTask


class TeamArtifactVerifier:
    version = "team-artifact-verifier-1.0.0"

    def verify(
        self, task: TeamTask, card: AgentCard, draft: ArtifactDraft
    ) -> TeamArtifact:
        """Build a content-addressed Artifact before Lead Context can consume it.

        Adapter output is treated as untrusted even when the Role is registered.
        Labels from every Part are joined monotonically; registration proves code
        provenance, not semantic truth.
        """

        if task.role_id != card.role_id:
            raise ValueError("Team Artifact role does not match its trusted adapter")
        if draft.used_tokens > task.allocated_tokens:
            raise ValueError("Team Artifact exceeds the task Token reservation")
        input_labels = [label for part in draft.parts for label in part.labels]
        joined = join_labels(
            input_labels,
            source=DataSource.SUBAGENT,
            source_ref=f"team-task:{task.id}:artifact",
        )
        canonical = {
            "task_id": task.id,
            "role_id": task.role_id,
            "parts": [item.model_dump(mode="json") for item in draft.parts],
            "metadata": draft.metadata,
        }
        digest = hashlib.sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return TeamArtifact(
            task_id=task.id,
            role_id=task.role_id,
            parts=draft.parts,
            labels=[joined],
            sha256=digest,
            verified=True,
            verifier_version=self.version,
            metadata=draft.metadata,
        )

    def revalidate(self, artifact: TeamArtifact) -> bool:
        canonical = {
            "task_id": artifact.task_id,
            "role_id": artifact.role_id,
            "parts": [item.model_dump(mode="json") for item in artifact.parts],
            "metadata": artifact.metadata,
        }
        digest = hashlib.sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return artifact.verified and digest == artifact.sha256
