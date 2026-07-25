"""Lesson collection routes."""

from fastapi import APIRouter

from atlas.api.dependencies import LessonsDependency
from atlas.api.schemas import LessonsResponse

router = APIRouter(prefix="/lessons", tags=["lessons"])


@router.get("", response_model=LessonsResponse)
def list_lessons(lessons: LessonsDependency) -> LessonsResponse:
    return lessons
