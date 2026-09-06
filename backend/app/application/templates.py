"""Deterministic curriculum used before LLM-generated content is introduced."""

from datetime import datetime

from app.application.curriculum import Curriculum
from app.domain.exercises import Exercise
from app.domain.goals import LearningGoal
from app.domain.knowledge import KnowledgeEdge, KnowledgeNode
from app.domain.plans import StudyPlan


def build_fixed_curriculum(goal: LearningGoal, *, now: datetime) -> Curriculum:
    specs = (
        (
            f"{goal.title}：基础与边界",
            f"识别“{goal.title}”的核心术语、适用范围和学习成功标准。",
            1.5,
            25,
            "学习一个新主题时，第一步最适合做什么？",
            ("明确核心概念与边界", "直接背诵所有细节", "跳过基础只做难题"),
            "明确核心概念与边界",
        ),
        (
            f"{goal.title}：核心理解",
            f"围绕目标“{goal.desired_outcome}”，建立概念之间的联系并能复述原理。",
            2.5,
            35,
            "下面哪种方式最能检验自己是否真正理解？",
            ("用自己的话解释并举例", "重复阅读同一段文字", "只记录学习时长"),
            "用自己的话解释并举例",
        ),
        (
            f"{goal.title}：应用与反馈",
            "在一个小任务中应用核心知识，根据结果定位错误并完成一次修正。",
            3.0,
            40,
            "形成稳定能力最有效的做法是什么？",
            ("完成练习并根据反馈修正", "只收藏更多资料", "避免暴露错误"),
            "完成练习并根据反馈修正",
        ),
    )
    nodes = tuple(
        KnowledgeNode.create(
            goal_id=goal.id,
            title=title,
            description=description,
            difficulty=difficulty,
            now=now,
        )
        for title, description, difficulty, _, _, _, _ in specs
    )
    edges = tuple(
        KnowledgeEdge.create(
            goal_id=goal.id,
            source_node_id=nodes[index].id,
            target_node_id=nodes[index + 1].id,
            now=now,
        )
        for index in range(len(nodes) - 1)
    )
    plan = StudyPlan.create(
        goal_id=goal.id,
        item_specs=[
            (node.id, title, estimated_minutes)
            for node, (
                title,
                _,
                _,
                estimated_minutes,
                _,
                _,
                _,
            ) in zip(nodes, specs, strict=True)
        ],
        now=now,
    )
    exercises = tuple(
        Exercise.create_multiple_choice(
            knowledge_node_id=node.id,
            prompt=prompt,
            options=list(options),
            answer_key=[answer],
            now=now,
        )
        for node, (_, _, _, _, prompt, options, answer) in zip(
            nodes, specs, strict=True
        )
    )
    return Curriculum(nodes=nodes, edges=edges, plan=plan, exercises=exercises)


class FixedCurriculumGenerator:
    async def generate(self, goal: LearningGoal, *, now: datetime) -> Curriculum:
        return build_fixed_curriculum(goal, now=now)
