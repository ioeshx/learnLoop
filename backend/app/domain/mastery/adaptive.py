"""Explainable rules for exercise difficulty and prerequisite remediation."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PrerequisiteMastery:
    knowledge_node_id: str
    title: str
    score: float


@dataclass(frozen=True, slots=True)
class AdaptiveRecommendation:
    mastery_score: float
    base_difficulty: float
    target_difficulty: float
    prerequisite_gaps: tuple[PrerequisiteMastery, ...]
    reasons: tuple[str, ...]


def recommend_difficulty(
    *,
    base_difficulty: float,
    mastery_score: float,
    prerequisites: tuple[PrerequisiteMastery, ...] = (),
) -> AdaptiveRecommendation:
    """Return a bounded, deterministic recommendation that can be shown to users."""
    mastery = min(1.0, max(0.0, mastery_score))
    gaps = tuple(item for item in prerequisites if item.score < 0.6)
    reasons: list[str] = []
    if mastery < 0.35:
        target = base_difficulty - 1.0
        reasons.append("当前掌握度低于 35%，练习难度下调一级")
    elif mastery >= 0.8:
        target = base_difficulty + 0.5
        reasons.append("当前掌握度达到 80%，练习难度上调半级")
    else:
        target = base_difficulty
        reasons.append("当前掌握度处于巩固区间，保持原难度")
    if gaps:
        target = min(target, base_difficulty - 0.5)
        reasons.append(f"检测到 {len(gaps)} 个掌握度低于 60% 的前置知识点，优先补救")
    return AdaptiveRecommendation(
        mastery_score=mastery,
        base_difficulty=base_difficulty,
        target_difficulty=min(5.0, max(1.0, target)),
        prerequisite_gaps=gaps,
        reasons=tuple(reasons),
    )
