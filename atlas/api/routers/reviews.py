"""Operator review-queue HTTP adapter."""

from fastapi import APIRouter

from atlas.api.dependencies import ReviewQueueDependency
from atlas.api.schemas import ReviewQueueResponse

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


@router.get("", response_model=ReviewQueueResponse)
def list_reviews(reviews: ReviewQueueDependency) -> ReviewQueueResponse:
    return reviews
