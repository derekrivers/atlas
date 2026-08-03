"""Lesson collection routes."""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from atlas.api.dependencies import (
    LessonsDependency,
    PromotedLessonDependency,
    RejectedLessonDependency,
)
from atlas.api.schemas import (
    LessonDispositionConflictResponse,
    LessonDispositionErrorResponse,
    LessonDispositionResponse,
    LessonsResponse,
)

router = APIRouter(prefix="/lessons", tags=["lessons"])
writable_router = APIRouter(prefix="/lessons", tags=["lessons"])

_LESSON_DISPOSITION_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": LessonDispositionErrorResponse},
    403: {"model": LessonDispositionErrorResponse},
    404: {"model": LessonDispositionErrorResponse},
    409: {"model": LessonDispositionConflictResponse},
    415: {"model": LessonDispositionErrorResponse},
    500: {"model": LessonDispositionErrorResponse},
}


@router.get("", response_model=LessonsResponse)
def list_lessons(lessons: LessonsDependency) -> LessonsResponse:
    return lessons


@writable_router.post(
    "/{lesson_id}/promote",
    response_model=LessonDispositionResponse,
    responses=_LESSON_DISPOSITION_RESPONSES,
)
def promote_lesson(
    disposition: PromotedLessonDependency,
) -> LessonDispositionResponse | JSONResponse:
    return disposition


@writable_router.post(
    "/{lesson_id}/reject",
    response_model=LessonDispositionResponse,
    responses=_LESSON_DISPOSITION_RESPONSES,
)
def reject_lesson(
    disposition: RejectedLessonDependency,
) -> LessonDispositionResponse | JSONResponse:
    return disposition
