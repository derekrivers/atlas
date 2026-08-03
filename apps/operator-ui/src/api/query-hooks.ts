import {
  useMutation,
  useQuery,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query'
import {
  atlasGet,
  atlasPromoteLesson,
  atlasRejectLesson,
  type AtlasApiRoute,
  type AtlasLessonDispositionResponse,
  type AtlasPromoteLessonRequest,
  type AtlasQueryError,
  type AtlasRejectLessonRequest,
  type AtlasRouteResponse,
} from '@/api/client'
import type { components } from '@/api/atlas-openapi'

type Schema = components['schemas']
type AtlasQueryResult<Path extends AtlasApiRoute> = UseQueryResult<
  AtlasRouteResponse<Path>,
  AtlasQueryError
>
type PromoteLessonVariables = {
  idempotencyKey: string
  lessonId: string
  request: AtlasPromoteLessonRequest
}
type RejectLessonVariables = {
  idempotencyKey: string
  lessonId: string
  request: AtlasRejectLessonRequest
}

export const ATLAS_QUERY_ROUTES = [
  '/api/v1/tickets',
  '/api/v1/tickets/count',
  '/api/v1/tickets/{key}',
  '/api/v1/tickets/{key}/evidence',
  '/api/v1/tickets/{key}/dependencies',
  '/api/v1/epics',
  '/api/v1/lessons',
  '/api/v1/dependencies/critical-path',
  '/api/v1/dependencies/graph',
  '/api/v1/reviews',
  '/api/v1/session',
  '/api/v1/status',
] as const satisfies readonly AtlasApiRoute[]

export const atlasQueryKeys = {
  criticalPath: () => ['atlas', 'dependencies', 'critical-path'] as const,
  dependencyGraph: () => ['atlas', 'dependencies', 'graph'] as const,
  epics: () => ['atlas', 'epics'] as const,
  lessons: (status?: Schema['EntityStatus'] | null) =>
    ['atlas', 'lessons', status ?? null] as const,
  reviews: () => ['atlas', 'reviews'] as const,
  session: () => ['atlas', 'session'] as const,
  status: () => ['atlas', 'status'] as const,
  ticketCount: () => ['atlas', 'tickets', 'count'] as const,
  ticketDependencies: (key: string) =>
    ['atlas', 'tickets', key, 'dependencies'] as const,
  ticketDetail: (key: string) => ['atlas', 'tickets', key] as const,
  ticketEvidence: (key: string) => ['atlas', 'tickets', key, 'evidence'] as const,
  tickets: (status?: Schema['TicketStatus'] | null) =>
    ['atlas', 'tickets', status ?? null] as const,
}

export function useTicketsQuery(
  status?: Schema['TicketStatus'] | null
): AtlasQueryResult<'/api/v1/tickets'> {
  return useQuery({
    queryKey: atlasQueryKeys.tickets(status),
    queryFn: () => atlasGet('/api/v1/tickets', { query: { status } }),
  })
}

export function useTicketCountQuery(): AtlasQueryResult<'/api/v1/tickets/count'> {
  return useQuery({
    queryKey: atlasQueryKeys.ticketCount(),
    queryFn: () => atlasGet('/api/v1/tickets/count'),
  })
}

export function useTicketDetailQuery(
  key: string
): AtlasQueryResult<'/api/v1/tickets/{key}'> {
  return useQuery({
    queryKey: atlasQueryKeys.ticketDetail(key),
    queryFn: () => atlasGet('/api/v1/tickets/{key}', { path: { key } }),
  })
}

export function useTicketEvidenceQuery(
  key: string
): AtlasQueryResult<'/api/v1/tickets/{key}/evidence'> {
  return useQuery({
    queryKey: atlasQueryKeys.ticketEvidence(key),
    queryFn: () =>
      atlasGet('/api/v1/tickets/{key}/evidence', { path: { key } }),
  })
}

export function useTicketDependenciesQuery(
  key: string
): AtlasQueryResult<'/api/v1/tickets/{key}/dependencies'> {
  return useQuery({
    queryKey: atlasQueryKeys.ticketDependencies(key),
    queryFn: () =>
      atlasGet('/api/v1/tickets/{key}/dependencies', { path: { key } }),
  })
}

export function useEpicsQuery(): AtlasQueryResult<'/api/v1/epics'> {
  return useQuery({
    queryKey: atlasQueryKeys.epics(),
    queryFn: () => atlasGet('/api/v1/epics'),
  })
}

export function useLessonsQuery(
  status?: Schema['EntityStatus'] | null
): AtlasQueryResult<'/api/v1/lessons'> {
  return useQuery({
    queryKey: atlasQueryKeys.lessons(status),
    queryFn: () => atlasGet('/api/v1/lessons', { query: { status } }),
  })
}

export function usePromoteLessonMutation(): UseMutationResult<
  AtlasLessonDispositionResponse,
  AtlasQueryError,
  PromoteLessonVariables
> {
  return useMutation({ mutationFn: atlasPromoteLesson })
}

export function useRejectLessonMutation(): UseMutationResult<
  AtlasLessonDispositionResponse,
  AtlasQueryError,
  RejectLessonVariables
> {
  return useMutation({ mutationFn: atlasRejectLesson })
}

export function useDependencyCriticalPathQuery(): AtlasQueryResult<'/api/v1/dependencies/critical-path'> {
  return useQuery({
    queryKey: atlasQueryKeys.criticalPath(),
    queryFn: () => atlasGet('/api/v1/dependencies/critical-path'),
  })
}

export function useDependencyGraphQuery(): AtlasQueryResult<'/api/v1/dependencies/graph'> {
  return useQuery({
    queryKey: atlasQueryKeys.dependencyGraph(),
    queryFn: () => atlasGet('/api/v1/dependencies/graph'),
  })
}

export function useReviewsQuery(): AtlasQueryResult<'/api/v1/reviews'> {
  return useQuery({
    queryKey: atlasQueryKeys.reviews(),
    queryFn: () => atlasGet('/api/v1/reviews'),
  })
}

export function useSessionQuery(): AtlasQueryResult<'/api/v1/session'> {
  return useQuery({
    queryKey: atlasQueryKeys.session(),
    queryFn: () => atlasGet('/api/v1/session'),
  })
}

export function useSystemStatusQuery(): AtlasQueryResult<'/api/v1/status'> {
  return useQuery({
    queryKey: atlasQueryKeys.status(),
    queryFn: () => atlasGet('/api/v1/status'),
  })
}
