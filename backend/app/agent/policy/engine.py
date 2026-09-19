"""Deterministic Policy Decision Point for Agent actions."""

from __future__ import annotations

import fnmatch
import hashlib
import json
from datetime import UTC, datetime

from app.agent.policy.models import (
    CapabilityGrant,
    PolicyAction,
    PolicyDecision,
    PolicyEffect,
    PolicyReason,
    PolicyRequest,
    Sensitivity,
)


class AgentPolicyEngine:
    """Evaluate authority, information-flow and approval rules in fixed order."""

    version = "agent-policy-1.0.0"

    def evaluate(
        self, request: PolicyRequest, *, now: datetime | None = None
    ) -> PolicyDecision:
        evaluated_at = now or datetime.now(UTC)
        fingerprint = _fingerprint(request)

        # Secret 不得进入 Model Context；对 Tool 只允许 read-only transformation，
        # 防止模型把 credential 通过 write Tool 或 remote sink 外带。
        if any(item.sensitivity == Sensitivity.SECRET for item in request.labels):
            if request.action == PolicyAction.MODEL_CONTEXT:
                return self._decision(
                    request,
                    fingerprint,
                    PolicyEffect.DENY,
                    PolicyReason.SECRET_TO_MODEL,
                )
            if request.action == PolicyAction.TOOL_EXECUTE and not request.read_only:
                return self._decision(
                    request,
                    fingerprint,
                    PolicyEffect.DENY,
                    PolicyReason.SECRET_TO_TOOL,
                )

        injection_signals = {
            signal for label in request.labels for signal in label.injection_signals
        }
        if injection_signals and not request.read_only:
            return self._decision(
                request,
                fingerprint,
                PolicyEffect.DENY,
                PolicyReason.INJECTION_TO_SIDE_EFFECT,
            )

        grant, grant_reason = _matching_grant(request, evaluated_at)
        if grant is None:
            return self._decision(
                request,
                fingerprint,
                PolicyEffect.DENY,
                grant_reason,
            )

        if request.risk == "high" and not request.approved:
            return self._decision(
                request,
                fingerprint,
                PolicyEffect.REQUIRE_APPROVAL,
                PolicyReason.HIGH_RISK_REQUIRES_APPROVAL,
                grant,
            )
        if request.approval_policy == "always" and not request.approved:
            return self._decision(
                request,
                fingerprint,
                PolicyEffect.REQUIRE_APPROVAL,
                PolicyReason.APPROVAL_REQUIRED,
                grant,
            )
        return self._decision(
            request,
            fingerprint,
            PolicyEffect.ALLOW,
            PolicyReason.ALLOWED,
            grant,
        )

    def _decision(
        self,
        request: PolicyRequest,
        fingerprint: str,
        effect: PolicyEffect,
        reason: PolicyReason,
        grant: CapabilityGrant | None = None,
    ) -> PolicyDecision:
        return PolicyDecision(
            policy_version=self.version,
            effect=effect,
            reason=reason,
            request_fingerprint=fingerprint,
            subject_id=request.subject.id,
            run_id=request.subject.run_id,
            action=request.action,
            capability=request.capability,
            resource=request.resource,
            matched_grant_id=grant.id if grant is not None else None,
            input_label_ids=sorted(item.id for item in request.labels),
        )


def _matching_grant(
    request: PolicyRequest, now: datetime
) -> tuple[CapabilityGrant | None, PolicyReason]:
    expired = False
    depth_exceeded = False
    for grant in request.grants:
        if grant.subject_id != request.subject.id:
            continue
        if grant.capability not in {request.capability, "*"}:
            continue
        if not fnmatch.fnmatchcase(request.resource, grant.resource_pattern):
            continue
        if grant.expires_at is not None and grant.expires_at <= now:
            expired = True
            continue
        if request.subject.delegation_depth > grant.max_delegation_depth:
            depth_exceeded = True
            continue
        return grant, PolicyReason.ALLOWED
    if expired:
        return None, PolicyReason.GRANT_EXPIRED
    if depth_exceeded:
        return None, PolicyReason.DELEGATION_DEPTH_EXCEEDED
    return None, PolicyReason.NO_CAPABILITY_GRANT


def _fingerprint(request: PolicyRequest) -> str:
    # Fingerprint 只包含授权 metadata，不序列化 arguments 或被保护的数据正文。
    canonical = {
        "subject": request.subject.model_dump(mode="json"),
        "action": request.action,
        "capability": request.capability,
        "resource": request.resource,
        "risk": request.risk,
        "read_only": request.read_only,
        "approval_policy": request.approval_policy,
        "approved": request.approved,
        "labels": [
            {
                "source": item.source,
                "source_ref": item.source_ref,
                "trust": item.trust,
                "sensitivity": item.sensitivity,
                "integrity": item.integrity,
                "signals": item.injection_signals,
            }
            for item in request.labels
        ],
        "grants": sorted(
            (
                item.subject_id,
                item.capability,
                item.resource_pattern,
                item.issuer,
                item.source,
                item.max_delegation_depth,
                item.expires_at.isoformat() if item.expires_at else None,
            )
            for item in request.grants
        ),
        "request_ref": request.request_ref,
    }
    return hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
