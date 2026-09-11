"""Explainable rules for exercise difficulty and prerequisite remediation."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PrerequisiteMastery:
    """一个前置知识点的掌握度读模型。

    该数据类把知识点标识、展示标题和当前分数交给自适应规则，用于判断学习者是否需要
    先补齐依赖；它不负责持久化，也不修改 MasterySnapshot。
    """

    knowledge_node_id: str
    title: str
    score: float


@dataclass(frozen=True, slots=True)
class AdaptiveRecommendation:
    """一次可解释的自适应难度决策结果。

    该数据类同时保留当前掌握度、知识点原始难度、规则计算出的目标难度、未掌握的前置
    知识点和面向用户的原因，供应用服务、Agent Tool、题目生成器和前端共同消费。
    """

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
    """根据掌握度和前置缺口生成有界、确定且可展示的难度建议。

    规则先把掌握度限制到 0～1，根据低掌握、巩固或高掌握区间调整难度；若存在低于
    60% 的前置知识点则进一步限制难度，最后把目标值约束在 1～5 并记录每条原因。
    """

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
