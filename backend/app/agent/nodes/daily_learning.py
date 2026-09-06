"""Nodes for the bounded daily-learning workflow."""

from typing import Literal, cast

from langgraph.runtime import Runtime

from app.agent.graphs.context import DailyLearningContext
from app.agent.prompts import LESSON_PROMPT
from app.agent.schemas import LessonContent
from app.agent.states import StudySessionState


async def load_context(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    session_id = _required_string(state, "session_id")
    session = await runtime.context.tools.get_study_session(session_id)
    node_id = cast(str, session["knowledge_node_id"])
    mastery = await runtime.context.tools.get_mastery_state(node_id)
    return {
        "run_id": state.get("run_id", session_id),
        "session_id": cast(str, session["session_id"]),
        "goal_id": cast(str, session["goal_id"]),
        "plan_id": cast(str, session["plan_id"]),
        "plan_item_id": cast(str, session["plan_item_id"]),
        "knowledge_node_id": node_id,
        "knowledge_node_title": cast(str, session["knowledge_node_title"]),
        "knowledge_node_description": cast(
            str, session["knowledge_node_description"]
        ),
        "knowledge_node_difficulty": cast(
            float, session["knowledge_node_difficulty"]
        ),
        "lesson_title": (
            state.get("lesson_title", cast(str, session["lesson_title"]))
            if state.get("remediation_count", 0) > 0
            else cast(str, session["lesson_title"])
        ),
        "lesson_content": (
            state.get("lesson_content", cast(str, session["lesson_content"]))
            if state.get("remediation_count", 0) > 0
            else cast(str, session["lesson_content"])
        ),
        "exercise_id": cast(str, session["exercise_id"]),
        "exercise_prompt": cast(str, session["exercise_prompt"]),
        "exercise_options": cast(list[str], session["exercise_options"]),
        "mastery_score": cast(float, mastery["score"]),
        "attempt_number": state.get("attempt_number", 0),
        "remediation_count": state.get("remediation_count", 0),
        "max_remediations": runtime.context.max_remediations,
        "status": "running",
        "events": ["load_context"],
    }


async def select_concepts(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    del runtime
    _required_string(state, "knowledge_node_id")
    return {"events": ["select_concepts"]}


async def retrieve_sources(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    node_id = _required_string(state, "knowledge_node_id")
    sources = await runtime.context.tools.search_learning_resources(node_id)
    return {"source_refs": sources, "events": ["retrieve_sources"]}


async def generate_lesson(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    # Preserve targeted remedial content when a caller resumes the explicit gate.
    if state.get("remediation_count", 0) > 0 and state.get("lesson_content"):
        return {"events": ["generate_lesson"]}
    if runtime.context.model is None:
        return {"events": ["generate_lesson"]}

    goal = await runtime.context.tools.get_learning_goal(
        _required_string(state, "goal_id")
    )
    result = await runtime.context.model.generate(
        LESSON_PROMPT,
        {
            "goal": cast(str, goal["desired_outcome"]),
            "knowledge_node_title": _required_string(
                state, "knowledge_node_title"
            ),
            "knowledge_node_description": _required_string(
                state, "knowledge_node_description"
            ),
            "difficulty": state.get("knowledge_node_difficulty", 1.0),
        },
        LessonContent,
    )
    return {
        "lesson_title": result.value.title,
        "lesson_content": _render_lesson(result.value),
        "events": ["generate_lesson"],
    }


async def generate_exercise(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    exercise = await runtime.context.tools.create_exercise(
        _required_string(state, "session_id")
    )
    return {
        "exercise_id": cast(str, exercise["exercise_id"]),
        "exercise_prompt": cast(str, exercise["prompt"]),
        "exercise_options": cast(list[str], exercise["options"]),
        "events": ["generate_exercise"],
    }


async def wait_for_answer(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    del runtime
    if state.get("selected_options"):
        return {"status": "running", "events": ["wait_for_answer"]}
    return {"status": "awaiting_answer", "events": ["wait_for_answer"]}


async def route_after_answer(
    state: StudySessionState,
) -> Literal["answer", "wait"]:
    return "answer" if state.get("selected_options") else "wait"


async def evaluate_answer(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    evaluation = await runtime.context.tools.grade_objective_answer(
        session_id=_required_string(state, "session_id"),
        exercise_id=_required_string(state, "exercise_id"),
        selected_options=state.get("selected_options", []),
    )
    return {"evaluation": evaluation, "events": ["evaluate_answer"]}


async def route_by_result(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    del runtime
    evaluation = state.get("evaluation", {})
    is_correct = evaluation.get("is_correct") is True
    remediation_count = state.get("remediation_count", 0)
    max_remediations = state.get("max_remediations", 2)
    outcome: Literal[
        "mastered", "partially_mastered", "not_mastered", "remediation_exhausted"
    ]
    if is_correct:
        outcome = "mastered"
    elif remediation_count >= max_remediations:
        outcome = "remediation_exhausted"
    elif remediation_count == 0:
        outcome = "partially_mastered"
    else:
        outcome = "not_mastered"
    return {"learning_outcome": outcome, "events": ["route_by_result"]}


async def update_mastery(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    attempt_number = state.get("attempt_number", 0)
    update = await runtime.context.tools.update_mastery(
        session_id=_required_string(state, "session_id"),
        exercise_id=_required_string(state, "exercise_id"),
        selected_options=state.get("selected_options", []),
        idempotency_key=(
            f"agent:{_required_string(state, 'run_id')}:attempt:{attempt_number}"
        ),
    )
    return {
        "evaluation": {**state.get("evaluation", {}), **update},
        "mastery_score": cast(float, update["mastery_score"]),
        "attempt_number": attempt_number + 1,
        "events": ["update_mastery"],
    }


async def schedule_review(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    due_at = runtime.context.tools.schedule_review(state.get("evaluation", {}))
    return {"review_due_at": due_at, "events": ["schedule_review"]}


async def route_after_review(
    state: StudySessionState,
) -> Literal["complete", "supplemental", "prerequisite"]:
    outcome = state.get("learning_outcome")
    if outcome == "partially_mastered":
        return "supplemental"
    if outcome == "not_mastered":
        return "prerequisite"
    return "complete"


async def generate_supplemental(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    content = await _generate_remediation(
        state,
        runtime,
        title_prefix="补充讲解",
        description_prefix="换一种方式解释，并针对刚才的错误给出一个新例子：",
    )
    return {
        "lesson_title": f"补充讲解：{_required_string(state, 'knowledge_node_title')}",
        "lesson_content": content,
        "selected_options": [],
        "remediation_count": state.get("remediation_count", 0) + 1,
        "status": "remediation",
        "events": ["generate_supplemental"],
    }


async def generate_prerequisite_remediation(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    content = await _generate_remediation(
        state,
        runtime,
        title_prefix="前置知识补救",
        description_prefix="先解释理解该知识点所需的前置概念，再回到原问题：",
    )
    return {
        "lesson_title": (
            f"前置知识补救：{_required_string(state, 'knowledge_node_title')}"
        ),
        "lesson_content": content,
        "selected_options": [],
        "remediation_count": state.get("remediation_count", 0) + 1,
        "status": "remediation",
        "events": ["generate_prerequisite_remediation"],
    }


async def save_summary(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    await runtime.context.tools.complete_study_session(
        _required_string(state, "session_id")
    )
    outcome = state.get("learning_outcome", "remediation_exhausted")
    summary = (
        f"已完成“{_required_string(state, 'knowledge_node_title')}”学习；"
        f"结果：{outcome}；当前掌握度：{state.get('mastery_score', 0.0):.2f}。"
    )
    return {
        "summary": summary,
        "status": "completed",
        "events": ["save_summary"],
    }


async def _generate_remediation(
    state: StudySessionState,
    runtime: Runtime[DailyLearningContext],
    *,
    title_prefix: str,
    description_prefix: str,
) -> str:
    if runtime.context.model is None:
        return (
            f"{description_prefix}\n\n"
            f"{_required_string(state, 'knowledge_node_description')}"
        )
    goal = await runtime.context.tools.get_learning_goal(
        _required_string(state, "goal_id")
    )
    result = await runtime.context.model.generate(
        LESSON_PROMPT,
        {
            "goal": cast(str, goal["desired_outcome"]),
            "knowledge_node_title": (
                f"{title_prefix}：{_required_string(state, 'knowledge_node_title')}"
            ),
            "knowledge_node_description": (
                f"{description_prefix}"
                f"{_required_string(state, 'knowledge_node_description')}"
            ),
            "difficulty": state.get("knowledge_node_difficulty", 1.0),
        },
        LessonContent,
    )
    return _render_lesson(result.value)


def _render_lesson(lesson: LessonContent) -> str:
    examples = "\n".join(f"- {example}" for example in lesson.examples)
    checkpoints = "\n".join(
        f"- {checkpoint}" for checkpoint in lesson.checkpoints
    )
    return (
        f"{lesson.explanation}\n\n"
        f"示例\n{examples}\n\n"
        f"自检\n{checkpoints}\n\n"
        f"总结\n{lesson.summary}"
    )


def _required_string(state: StudySessionState, key: str) -> str:
    value = state.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"daily-learning state requires '{key}'")
    return value
