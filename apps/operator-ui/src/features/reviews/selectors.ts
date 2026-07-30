import type { AtlasRouteResponse } from '@/api/client'

export type ReviewQueueResponse = AtlasRouteResponse<'/api/v1/reviews'>
export type ReviewQueueItem = ReviewQueueResponse['reviews'][number]

export function selectReviewQueueItems(
  response: ReviewQueueResponse
): ReviewQueueItem[] {
  return response.reviews
}

export function selectReviewQueueDepth(response: ReviewQueueResponse): number {
  return response.reviews.length
}
