import { readFileSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import type { UseQueryResult } from '@tanstack/react-query'
import type { components } from '@/api/atlas-openapi'
import {
  ATLAS_QUERY_ROUTES,
  type useAcceptanceSessionQuery,
  type useConfirmAcceptanceSessionMutation,
  type useCreateAcceptanceSessionMutation,
  type useDependencyCriticalPathQuery,
  type useDependencyGraphQuery,
  type useEpicsQuery,
  type useLessonsQuery,
  type usePromoteLessonMutation,
  type usePullAcceptanceEvidenceMutation,
  type useRejectLessonMutation,
  type useReviewsQuery,
  type useSessionQuery,
  type useSystemStatusQuery,
  type useTicketCountQuery,
  type useTicketDependenciesQuery,
  type useTicketDetailQuery,
  type useTicketEvidenceQuery,
  type useTicketsQuery,
  type useVerifyAcceptanceSessionMutation,
} from '@/api/query-hooks'
import type {
  AtlasApiRoute,
  AtlasQueryError,
  AtlasRouteResponse,
} from '@/api/client'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const authoredApiFiles = ['src/api/client.ts', 'src/api/query-hooks.ts']

type Equal<Left, Right> = (<Value>() => Value extends Left ? 1 : 2) extends <
  Value,
>() => Value extends Right ? 1 : 2
  ? true
  : false
type Assert<Type extends true> = Type
type IsAny<Value> = 0 extends 1 & Value ? true : false
type NotAny<Value> = Equal<IsAny<Value>, false>
type HookData<
  Hook extends (...args: never[]) => UseQueryResult<unknown, AtlasQueryError>,
> = NonNullable<ReturnType<Hook>['data']>
type MutationData<
  Hook extends (...args: never[]) => object,
> = ReturnType<Hook> extends { data: infer Data } ? NonNullable<Data> : never

type CoveredRoute = (typeof ATLAS_QUERY_ROUTES)[number]
type HookResponseParity = [
  Assert<Equal<HookData<typeof useTicketsQuery>, AtlasRouteResponse<'/api/v1/tickets'>>>,
  Assert<
    Equal<
      HookData<typeof useTicketCountQuery>,
      AtlasRouteResponse<'/api/v1/tickets/count'>
    >
  >,
  Assert<
    Equal<
      HookData<typeof useTicketDetailQuery>,
      AtlasRouteResponse<'/api/v1/tickets/{key}'>
    >
  >,
  Assert<
    Equal<
      HookData<typeof useTicketEvidenceQuery>,
      AtlasRouteResponse<'/api/v1/tickets/{key}/evidence'>
    >
  >,
  Assert<
    Equal<
      HookData<typeof useTicketDependenciesQuery>,
      AtlasRouteResponse<'/api/v1/tickets/{key}/dependencies'>
    >
  >,
  Assert<Equal<HookData<typeof useEpicsQuery>, AtlasRouteResponse<'/api/v1/epics'>>>,
  Assert<
    Equal<HookData<typeof useLessonsQuery>, AtlasRouteResponse<'/api/v1/lessons'>>
  >,
  Assert<
    Equal<
      HookData<typeof useDependencyCriticalPathQuery>,
      AtlasRouteResponse<'/api/v1/dependencies/critical-path'>
    >
  >,
  Assert<
    Equal<
      HookData<typeof useDependencyGraphQuery>,
      AtlasRouteResponse<'/api/v1/dependencies/graph'>
    >
  >,
  Assert<
    Equal<HookData<typeof useReviewsQuery>, AtlasRouteResponse<'/api/v1/reviews'>>
  >,
  Assert<
    Equal<
      HookData<typeof useAcceptanceSessionQuery>,
      AtlasRouteResponse<'/api/v1/acceptance-sessions/{session_id}'>
    >
  >,
  Assert<
    Equal<HookData<typeof useSessionQuery>, AtlasRouteResponse<'/api/v1/session'>>
  >,
  Assert<
    Equal<HookData<typeof useSystemStatusQuery>, AtlasRouteResponse<'/api/v1/status'>>
  >,
]
type HookAnyGuard = [
  Assert<NotAny<HookData<typeof useTicketsQuery>>>,
  Assert<NotAny<HookData<typeof useTicketCountQuery>>>,
  Assert<NotAny<HookData<typeof useTicketDetailQuery>>>,
  Assert<NotAny<HookData<typeof useTicketEvidenceQuery>>>,
  Assert<NotAny<HookData<typeof useTicketDependenciesQuery>>>,
  Assert<NotAny<HookData<typeof useEpicsQuery>>>,
  Assert<NotAny<HookData<typeof useLessonsQuery>>>,
  Assert<NotAny<HookData<typeof useDependencyCriticalPathQuery>>>,
  Assert<NotAny<HookData<typeof useDependencyGraphQuery>>>,
  Assert<NotAny<HookData<typeof useReviewsQuery>>>,
  Assert<NotAny<HookData<typeof useAcceptanceSessionQuery>>>,
  Assert<NotAny<HookData<typeof useSessionQuery>>>,
  Assert<NotAny<HookData<typeof useSystemStatusQuery>>>,
]
type MutationResponseParity = [
  Assert<
    Equal<
      MutationData<typeof usePromoteLessonMutation>,
      components['schemas']['LessonDispositionResponse']
    >
  >,
  Assert<
    Equal<
      MutationData<typeof useRejectLessonMutation>,
      components['schemas']['LessonDispositionResponse']
    >
  >,
  Assert<
    Equal<
      MutationData<typeof useCreateAcceptanceSessionMutation>,
      components['schemas']['AcceptanceSessionCreationResponse']
    >
  >,
  Assert<
    Equal<
      MutationData<typeof usePullAcceptanceEvidenceMutation>,
      components['schemas']['AcceptanceSessionActionResponse']
    >
  >,
  Assert<
    Equal<
      MutationData<typeof useConfirmAcceptanceSessionMutation>,
      components['schemas']['AcceptanceSessionActionResponse']
    >
  >,
  Assert<
    Equal<
      MutationData<typeof useVerifyAcceptanceSessionMutation>,
      components['schemas']['AcceptanceSessionActionResponse']
    >
  >,
]

const routeCoverage: Assert<Equal<CoveredRoute, AtlasApiRoute>> = true
const hookResponseParity: HookResponseParity = [
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
]
const hookAnyGuard: HookAnyGuard = [
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
  true,
]
const mutationResponseParity: MutationResponseParity = [
  true,
  true,
  true,
  true,
  true,
  true,
]

describe('typed Atlas query hooks', () => {
  it('covers every generated current v1 GET route', () => {
    expect(routeCoverage).toBe(true)
    expect(ATLAS_QUERY_ROUTES).toHaveLength(13)
  })

  it('returns generated response types without authored any escapes', () => {
    expect(hookResponseParity.every((value) => value)).toBe(true)
    expect(hookAnyGuard.every((value) => value)).toBe(true)

    for (const file of authoredApiFiles) {
      const path = join(appRoot, file)
      expect(
        readFileSync(path, 'utf8'),
        `${relative(appRoot, path)} must not use any`
      ).not.toMatch(/\bany\b/)
    }
  })

  it('types governed mutations from generated response contracts', () => {
    expect(mutationResponseParity.every((value) => value)).toBe(true)
  })
})
