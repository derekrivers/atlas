import type { AtlasRouteResponse } from '@/api/client'

export type CriticalPathResponse =
  AtlasRouteResponse<'/api/v1/dependencies/critical-path'>
export type CriticalPathStep = CriticalPathResponse['steps'][number]

export function selectCriticalPathSteps(
  response: CriticalPathResponse
): CriticalPathStep[] {
  return response.steps
}

export function selectCriticalPathHead(
  response: CriticalPathResponse
): CriticalPathStep | null {
  return response.steps[0] ?? null
}

export function selectCriticalPathTotalEffort(
  response: CriticalPathResponse
): number {
  return response.total_effort
}
