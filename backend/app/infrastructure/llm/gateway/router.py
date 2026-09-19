"""Deterministic capability, budget, residency and deadline Model router."""

from __future__ import annotations

from app.infrastructure.llm.gateway.models import (
    ModelProfile,
    ModelRequirement,
    RouteCandidate,
    RoutePlan,
)


class CapabilityRouter:
    def __init__(self, profiles: list[ModelProfile]) -> None:
        ids = [item.provider_id for item in profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("Model provider_id values must be unique")
        self._profiles = {item.provider_id: item for item in profiles}

    @property
    def profiles(self) -> list[ModelProfile]:
        return list(self._profiles.values())

    def route(
        self,
        requirement: ModelRequirement,
        *,
        unavailable: set[str] | None = None,
        preferred_provider_id: str | None = None,
    ) -> RoutePlan:
        """Filter unsafe routes first, then rank valid candidates deterministically.

        The score deliberately contains only trusted profile metadata. Provider
        marketing names or model-generated hints cannot influence routing.
        """

        unavailable_ids = unavailable or set()
        candidates: list[RouteCandidate] = []
        rejected: dict[str, list[str]] = {}
        for profile in self._profiles.values():
            reasons = _rejection_reasons(profile, requirement, unavailable_ids)
            if reasons:
                rejected[profile.provider_id] = reasons
                continue
            estimated_cost = estimate_cost(profile, requirement)
            preferred_rank = 0 if profile.provider_id == preferred_provider_id else 1
            # Lower tuple wins: route affinity, higher priority, latency, cost, id.
            score = (
                preferred_rank * 1_000 - profile.priority,
                profile.expected_latency_ms,
                estimated_cost,
                profile.provider_id,
            )
            candidates.append(
                RouteCandidate(
                    provider_id=profile.provider_id,
                    model=profile.model,
                    estimated_cost_usd=estimated_cost,
                    score=score,
                    explanation=[
                        "all_required_capabilities_matched",
                        "context_and_output_fit",
                        "cost_preflight_passed",
                        "deadline_preflight_passed",
                        (
                            "route_affinity"
                            if profile.provider_id == preferred_provider_id
                            else "deterministic_score"
                        ),
                    ],
                )
            )
        candidates.sort(key=lambda item: item.score)
        return RoutePlan(candidates=candidates, rejected=rejected)


def estimate_cost(profile: ModelProfile, requirement: ModelRequirement) -> float:
    return round(
        requirement.estimated_input_tokens
        * profile.input_cost_per_million_usd
        / 1_000_000
        + requirement.max_output_tokens
        * profile.output_cost_per_million_usd
        / 1_000_000,
        9,
    )


def _rejection_reasons(
    profile: ModelProfile,
    requirement: ModelRequirement,
    unavailable: set[str],
) -> list[str]:
    reasons: list[str] = []
    if not profile.enabled:
        reasons.append("profile_disabled")
    missing = requirement.required_capabilities - profile.capabilities
    if missing:
        reasons.append("missing_capabilities:" + ",".join(sorted(missing)))
    if (
        requirement.estimated_input_tokens + requirement.max_output_tokens
        > profile.context_window
    ):
        reasons.append("context_window_exceeded")
    if requirement.max_output_tokens > profile.max_output_tokens:
        reasons.append("max_output_exceeded")
    if (
        requirement.data_residency is not None
        and profile.data_residency != requirement.data_residency
    ):
        reasons.append("data_residency_mismatch")
    cost = estimate_cost(profile, requirement)
    if (
        requirement.max_estimated_cost_usd is not None
        and cost > requirement.max_estimated_cost_usd
    ):
        reasons.append("estimated_cost_exceeded")
    if (
        requirement.deadline_ms is not None
        and profile.expected_latency_ms > requirement.deadline_ms
    ):
        reasons.append("deadline_preflight_failed")
    if profile.provider_id in unavailable:
        reasons.append("circuit_unavailable")
    return reasons
