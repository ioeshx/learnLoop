"""Study session and exercise attempt endpoints."""

from typing import Annotated

from fastapi import APIRouter, Header, status

from app.api.dependencies import ApplicationDependenciesDep
from app.api.schemas import (
    AttemptResultResponse,
    SessionResponse,
    StartSessionRequest,
    SubmitAttemptRequest,
)
from app.application import (
    CompleteStudySession,
    GetStudySession,
    StartSessionCommand,
    StartStudySession,
    SubmitAttemptCommand,
    SubmitExerciseAttempt,
)

router = APIRouter(prefix="/study-sessions")
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=200,
        pattern=r".*\S.*",
    ),
]


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def start_session(
    payload: StartSessionRequest,
    idempotency_key: IdempotencyKey,
    dependencies: ApplicationDependenciesDep,
) -> SessionResponse:
    details = await StartStudySession(dependencies).execute(
        StartSessionCommand(
            goal_id=payload.goal_id,
            plan_item_id=payload.plan_item_id,
            idempotency_key=idempotency_key,
        )
    )
    return SessionResponse.from_details(details)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str, dependencies: ApplicationDependenciesDep
) -> SessionResponse:
    details = await GetStudySession(dependencies).execute(session_id)
    return SessionResponse.from_details(details)


@router.post(
    "/{session_id}/attempts",
    response_model=AttemptResultResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_attempt(
    session_id: str,
    payload: SubmitAttemptRequest,
    idempotency_key: IdempotencyKey,
    dependencies: ApplicationDependenciesDep,
) -> AttemptResultResponse:
    result = await SubmitExerciseAttempt(dependencies).execute(
        SubmitAttemptCommand(
            session_id=session_id,
            exercise_id=payload.exercise_id,
            selected_options=tuple(payload.selected_options),
            idempotency_key=idempotency_key,
        )
    )
    return AttemptResultResponse.from_result(result)


@router.post("/{session_id}/complete", response_model=SessionResponse)
async def complete_session(
    session_id: str, dependencies: ApplicationDependenciesDep
) -> SessionResponse:
    details = await CompleteStudySession(dependencies).execute(session_id)
    return SessionResponse.from_details(details)
