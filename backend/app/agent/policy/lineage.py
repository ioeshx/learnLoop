"""Monotonic data-label propagation for Agent information flows."""

from __future__ import annotations

from app.agent.policy.models import (
    DataLabel,
    DataSource,
    IntegrityLevel,
    Sensitivity,
    TrustLevel,
)

_TRUST_RANK = {
    TrustLevel.UNTRUSTED: 0,
    TrustLevel.USER_ASSERTED: 1,
    TrustLevel.VERIFIED: 2,
    TrustLevel.SYSTEM: 3,
}
_SENSITIVITY_RANK = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.INTERNAL: 1,
    Sensitivity.PERSONAL: 2,
    Sensitivity.SECRET: 3,
}
_INTEGRITY_RANK = {
    IntegrityLevel.UNVERIFIED: 0,
    IntegrityLevel.HASHED: 1,
    IntegrityLevel.VERIFIED: 2,
}


def join_labels(
    labels: list[DataLabel],
    *,
    source: DataSource,
    source_ref: str,
) -> DataLabel:
    """Join labels without allowing trust or integrity escalation.

    Information-flow join uses the least trusted/verified input and the most
    sensitive input. A Tool may validate a value later, but that requires a new
    explicit Verifier label; ordinary transformation cannot silently upgrade it.
    """

    if not labels:
        return DataLabel(
            source=source,
            source_ref=source_ref,
            trust=TrustLevel.UNTRUSTED,
            sensitivity=Sensitivity.INTERNAL,
        )
    trust = min(labels, key=lambda item: _TRUST_RANK[item.trust]).trust
    sensitivity = max(
        labels, key=lambda item: _SENSITIVITY_RANK[item.sensitivity]
    ).sensitivity
    integrity = min(
        labels, key=lambda item: _INTEGRITY_RANK[item.integrity]
    ).integrity
    signals = sorted(
        {signal for item in labels for signal in item.injection_signals},
        key=str,
    )
    return DataLabel(
        source=source,
        source_ref=source_ref,
        trust=trust,
        sensitivity=sensitivity,
        integrity=integrity,
        injection_signals=signals,
        parent_label_ids=sorted({item.id for item in labels}),
    )


def attenuate_grant_resource(parent_pattern: str, child_pattern: str) -> bool:
    """Conservative scope attenuation used before delegated grants are issued."""

    if parent_pattern == "*":
        return True
    if parent_pattern.endswith("*"):
        return child_pattern.startswith(parent_pattern[:-1])
    return child_pattern == parent_pattern
