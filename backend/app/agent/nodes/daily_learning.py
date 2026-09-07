"""Nodes for the bounded daily-learning workflow."""

from typing import Literal, cast

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from app.agent.graphs.context import DailyLearningContext
from app.agent.nodes.tool_events import call_tool
from app.agent.prompts import (
    ANSWER_EVALUATION_PROMPT,
    LESSON_PROMPT,
    MISCONCEPTION_DIAGNOSIS_PROMPT,
)
from app.agent.schemas import (
    AnswerEvaluation,
    LessonContent,
    MisconceptionDiagnosis,
)
from app.agent.states import StudySessionState


async def load_context(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    session_id = _required_string(state, "session_id")
    session = await call_tool(
        runtime,
        "get_study_session",
        runtime.context.tools.get_study_session(session_id),
    )
    node_id = cast(str, session["knowledge_node_id"])
    mastery = await call_tool(
        runtime,
        "get_mastery_state",
        runtime.context.tools.get_mastery_state(node_id),
    )
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
    sources = await call_tool(
        runtime,
        "search_learning_resources",
        runtime.context.tools.search_learning_resources(node_id),
    )
    return {"source_refs": sources, "events": ["retrieve_sources"]}


async def generate_lesson(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    # Preserve targeted remedial content when a caller resumes the explicit gate.
    if state.get("remediation_count", 0) > 0 and state.get("lesson_content"):
        return {"events": ["generate_lesson"]}
    source_refs = state.get("source_refs", [])
    if runtime.context.model is None:
        appendix = _citation_appendix(source_refs)
        lesson_content = state.get("lesson_content", "")
        return {
            "lesson_content": (
                f"{lesson_content.rstrip()}{appendix}"
                if appendix
                else lesson_content
            ),
            "events": ["generate_lesson"],
        }

    goal = await call_tool(
        runtime,
        "get_learning_goal",
        runtime.context.tools.get_learning_goal(
            _required_string(state, "goal_id")
        ),
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
            "sources": _source_prompt_values(source_refs),
        },
        LessonContent,
    )
    return {
        "lesson_title": result.value.title,
        "lesson_content": _render_lesson(result.value, source_refs),
        "events": ["generate_lesson"],
    }


async def generate_exercise(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    exercise = await call_tool(
        runtime,
        "create_exercise",
        runtime.context.tools.create_exercise(
            _required_string(state, "session_id")
        ),
    )
    return {
        "exercise_id": cast(str, exercise["exercise_id"]),
        "exercise_prompt": (
            f"{cast(str, exercise['prompt'])}"
            f"{_citation_appendix(state.get('source_refs', []), heading='支持资料')}"
        ),
        "exercise_options": cast(list[str], exercise["options"]),
        "status": "awaiting_answer",
        "events": ["generate_exercise"],
    }


async def wait_for_answer(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    del runtime
    if state.get("selected_options"):
        return {"status": "running", "events": ["wait_for_answer"]}
    response = interrupt(
        {
            "type": "answer_required",
            "run_id": state.get("run_id"),
            "session_id": state.get("session_id"),
            "exercise": {
                "id": state.get("exercise_id"),
                "prompt": state.get("exercise_prompt"),
                "options": state.get("exercise_options", []),
            },
            "remediation_count": state.get("remediation_count", 0),
        }
    )
    selected_options = _resume_selected_options(response)
    return {
        "selected_options": selected_options,
        "status": "running",
        "events": ["wait_for_answer"],
    }


async def route_after_answer(
    state: StudySessionState,
) -> Literal["answer", "wait"]:
    return "answer" if state.get("selected_options") else "wait"


async def evaluate_answer(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    evaluation = await call_tool(
        runtime,
        "grade_objective_answer",
        runtime.context.tools.grade_objective_answer(
            session_id=_required_string(state, "session_id"),
            exercise_id=_required_string(state, "exercise_id"),
            selected_options=state.get("selected_options", []),
        ),
    )
    return {
        "evaluation": evaluation,
        "status": (
            "awaiting_grade_review"
            if runtime.context.review_grades
            else "running"
        ),
        "events": ["evaluate_answer"],
    }


async def review_grade(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    if not runtime.context.review_grades:
        return {"events": ["review_grade"]}
    response = interrupt(
        {
            "type": "grade_review_required",
            "run_id": state.get("run_id"),
            "session_id": state.get("session_id"),
            "evaluation": state.get("evaluation", {}),
            "selected_options": state.get("selected_options", []),
            "allowed_actions": ["accept", "revise_answer"],
        }
    )
    if not isinstance(response, dict):
        raise ValueError("grade review response must be an object")
    action = response.get("action")
    if action == "accept":
        return {
            "grade_review": {"action": "accept"},
            "status": "running",
            "events": ["review_grade"],
        }
    if action == "revise_answer":
        selected_options = _resume_selected_options(response)
        evaluation = await call_tool(
            runtime,
            "grade_objective_answer",
            runtime.context.tools.grade_objective_answer(
                session_id=_required_string(state, "session_id"),
                exercise_id=_required_string(state, "exercise_id"),
                selected_options=selected_options,
            ),
        )
        return {
            "selected_options": selected_options,
            "evaluation": evaluation,
            "grade_review": {"action": "revise_answer"},
            "status": "running",
            "events": ["review_grade"],
        }
    raise ValueError("grade review action must be 'accept' or 'revise_answer'")


async def evaluate_short_answer(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    """Reusable LLM node for future short-answer exercise types."""
    if runtime.context.model is None:
        raise ValueError("short-answer evaluation requires a model")
    result = await runtime.context.model.generate(
        ANSWER_EVALUATION_PROMPT,
        {
            "question": _required_string(state, "exercise_prompt"),
            "rubric": _required_string(state, "evaluation_rubric"),
            "reference_answer": _required_string(state, "reference_answer"),
            "learner_answer": _required_string(state, "learner_answer"),
        },
        AnswerEvaluation,
    )
    return {
        "evaluation": result.value.model_dump(mode="json"),
        "events": ["evaluate_short_answer"],
    }


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
    update = await call_tool(
        runtime,
        "update_mastery",
        runtime.context.tools.update_mastery(
            session_id=_required_string(state, "session_id"),
            exercise_id=_required_string(state, "exercise_id"),
            selected_options=state.get("selected_options", []),
            idempotency_key=(
                f"agent:{_required_string(state, 'run_id')}:attempt:{attempt_number}"
            ),
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


async def diagnose_error(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    expected = state.get("evaluation", {}).get("expected_answer", [])
    if not isinstance(expected, list) or not all(
        isinstance(option, str) for option in expected
    ):
        raise ValueError("objective evaluation requires expected answers")
    if runtime.context.model is None:
        diagnosis: dict[str, object] = {
            "misconception": "本次答案与标准答案不一致。",
            "evidence": [
                f"选择：{', '.join(state.get('selected_options', []))}",
                f"标准答案：{', '.join(expected)}",
            ],
            "remediation_steps": ["重新比较每个选项与知识点定义。"],
            "prerequisite_node_keys": [],
        }
    else:
        result = await runtime.context.model.generate(
            MISCONCEPTION_DIAGNOSIS_PROMPT,
            {
                "knowledge_node": _required_string(
                    state, "knowledge_node_title"
                ),
                "question": _required_string(state, "exercise_prompt"),
                "expected_answer": expected,
                "learner_answer": state.get("selected_options", []),
            },
            MisconceptionDiagnosis,
        )
        diagnosis = result.value.model_dump(mode="json")
    return {"diagnosis": diagnosis, "events": ["diagnose_error"]}


async def route_after_review(
    state: StudySessionState,
) -> Literal["complete", "remediate"]:
    outcome = state.get("learning_outcome")
    return (
        "remediate"
        if outcome in {"partially_mastered", "not_mastered"}
        else "complete"
    )


async def route_after_diagnosis(
    state: StudySessionState,
) -> Literal["supplemental", "prerequisite"]:
    return (
        "supplemental"
        if state.get("learning_outcome") == "partially_mastered"
        else "prerequisite"
    )


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
        "status": "awaiting_answer",
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
        "status": "awaiting_answer",
        "events": ["generate_prerequisite_remediation"],
    }


async def save_summary(
    state: StudySessionState, runtime: Runtime[DailyLearningContext]
) -> StudySessionState:
    await call_tool(
        runtime,
        "complete_study_session",
        runtime.context.tools.complete_study_session(
            _required_string(state, "session_id")
        ),
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
    diagnosis = state.get("diagnosis", {})
    misconception = diagnosis.get("misconception", "")
    diagnosis_context = (
        f"\n本次错因诊断：{misconception}"
        if isinstance(misconception, str) and misconception
        else ""
    )
    if runtime.context.model is None:
        content = (
            f"{description_prefix}\n\n"
            f"{_required_string(state, 'knowledge_node_description')}"
            f"{diagnosis_context}"
        )
        return f"{content}{_citation_appendix(state.get('source_refs', []))}"
    goal = await call_tool(
        runtime,
        "get_learning_goal",
        runtime.context.tools.get_learning_goal(
            _required_string(state, "goal_id")
        ),
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
                f"{diagnosis_context}"
            ),
            "difficulty": state.get("knowledge_node_difficulty", 1.0),
            "sources": _source_prompt_values(state.get("source_refs", [])),
        },
        LessonContent,
    )
    return _render_lesson(result.value, state.get("source_refs", []))


def _render_lesson(
    lesson: LessonContent, source_refs: list[dict[str, object]] | None = None
) -> str:
    examples = "\n".join(f"- {example}" for example in lesson.examples)
    checkpoints = "\n".join(
        f"- {checkpoint}" for checkpoint in lesson.checkpoints
    )
    return (
        f"{lesson.explanation}\n\n"
        f"示例\n{examples}\n\n"
        f"自检\n{checkpoints}\n\n"
        f"总结\n{lesson.summary}"
        f"{_citation_appendix(source_refs or [])}"
    )


def _source_prompt_values(
    source_refs: list[dict[str, object]],
) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for source in source_refs[:5]:
        excerpt = source.get("excerpt")
        title = source.get("title")
        if not isinstance(excerpt, str) or not isinstance(title, str):
            continue
        values.append(
            {
                "title": title,
                "excerpt": excerpt[:1_500],
                "page_number": source.get("page_number"),
                "section": source.get("section"),
            }
        )
    return values


def _citation_appendix(
    source_refs: list[dict[str, object]], *, heading: str = "参考资料"
) -> str:
    citations: list[str] = []
    for index, source in enumerate(source_refs[:5], start=1):
        title = source.get("title")
        if not isinstance(title, str):
            continue
        page = source.get("page_number")
        section = source.get("section")
        if isinstance(page, int):
            locator = f"第 {page} 页"
        elif isinstance(section, str) and section:
            locator = section
        else:
            locator = "全文"
        citations.append(f"[{index}] {title} · {locator}")
    if not citations:
        return ""
    return f"\n\n{heading}\n" + "\n".join(f"- {item}" for item in citations)


def _required_string(state: StudySessionState, key: str) -> str:
    value = state.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"daily-learning state requires '{key}'")
    return value


def _resume_selected_options(response: object) -> list[str]:
    if isinstance(response, dict):
        selected = response.get("selected_options")
    else:
        selected = response
    if not isinstance(selected, list) or not selected or not all(
        isinstance(option, str) and option.strip() for option in selected
    ):
        raise ValueError("resume value requires non-empty selected_options")
    return selected
