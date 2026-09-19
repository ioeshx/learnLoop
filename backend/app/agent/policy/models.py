"""Strict contracts for the v3 Agent Trust and Policy control plane."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PolicyContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TrustLevel(StrEnum):
    UNTRUSTED = "untrusted"
    USER_ASSERTED = "user_asserted"
    VERIFIED = "verified"
    SYSTEM = "system"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    PERSONAL = "personal"
    SECRET = "secret"


class IntegrityLevel(StrEnum):
    UNVERIFIED = "unverified"
    HASHED = "hashed"
    VERIFIED = "verified"


class DataSource(StrEnum):
    USER = "user"
    MODEL = "model"
    TOOL = "tool"
    RESOURCE = "resource"
    MEMORY = "memory"
    SUBAGENT = "subagent"
    SYSTEM = "system"


class InjectionSignal(StrEnum):
    INSTRUCTION_LIKE = "instruction_like"
    CREDENTIAL_REQUEST = "credential_request"
    AUTHORITY_CLAIM = "authority_claim"
    DATA_EXFILTRATION = "data_exfiltration"


class DataLabel(PolicyContract):
    """Metadata-only information-flow label; never contains the protected value."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    source: DataSource
    source_ref: str = Field(min_length=1, max_length=500)
    trust: TrustLevel
    sensitivity: Sensitivity
    integrity: IntegrityLevel = IntegrityLevel.UNVERIFIED
    injection_signals: list[InjectionSignal] = Field(default_factory=list)
    parent_label_ids: list[str] = Field(default_factory=list, max_length=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        if self.trust == TrustLevel.SYSTEM and self.source != DataSource.SYSTEM:
            raise ValueError("Only the system source may assert system trust")
        if len(self.injection_signals) != len(set(self.injection_signals)):
            raise ValueError("Injection signals must be unique")
        return self


class PolicySubject(PolicyContract):
    id: str = Field(min_length=1, max_length=300)
    kind: str = Field(pattern=r"^(lead_agent|subagent|user|system)$")
    run_id: str | None = None
    parent_subject_id: str | None = None
    delegation_depth: int = Field(default=0, ge=0, le=8)


class CapabilityGrant(PolicyContract):
    """Authority issued by trusted runtime state, never by model output."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    subject_id: str
    capability: str = Field(pattern=r"^[a-z][a-z0-9_.:*\-]{1,199}$")
    resource_pattern: str = Field(default="*", min_length=1, max_length=500)
    issuer: str = Field(default="learnloop-runtime", min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=300)
    max_delegation_depth: int = Field(default=0, ge=0, le=8)
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PolicyAction(StrEnum):
    TOOL_EXECUTE = "tool_execute"
    MODEL_CONTEXT = "model_context"
    DELEGATE = "delegate"
    ARTIFACT_IMPORT = "artifact_import"


class PolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class PolicyReason(StrEnum):
    ALLOWED = "allowed"
    NO_CAPABILITY_GRANT = "no_capability_grant"
    GRANT_EXPIRED = "grant_expired"
    DELEGATION_DEPTH_EXCEEDED = "delegation_depth_exceeded"
    SECRET_TO_MODEL = "secret_to_model"
    SECRET_TO_TOOL = "secret_to_tool"
    INJECTION_TO_SIDE_EFFECT = "injection_to_side_effect"
    APPROVAL_REQUIRED = "approval_required"
    HIGH_RISK_REQUIRES_APPROVAL = "high_risk_requires_approval"


class PolicyRequest(PolicyContract):
    subject: PolicySubject
    action: PolicyAction
    capability: str
    resource: str = Field(min_length=1, max_length=500)
    risk: str = Field(default="low", pattern=r"^(low|medium|high)$")
    read_only: bool = True
    approval_policy: str = Field(
        default="never", pattern=r"^(never|when_requested|always)$"
    )
    approved: bool = False
    labels: list[DataLabel] = Field(default_factory=list, max_length=100)
    grants: list[CapabilityGrant] = Field(default_factory=list, max_length=100)
    request_ref: str = Field(min_length=1, max_length=500)


class PolicyDecision(PolicyContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    policy_version: str = "agent-policy-1.0.0"
    effect: PolicyEffect
    reason: PolicyReason
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject_id: str
    run_id: str | None = None
    action: PolicyAction
    capability: str
    resource: str
    matched_grant_id: str | None = None
    input_label_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PolicySimulationRequest(PolicyContract):
    request: PolicyRequest
