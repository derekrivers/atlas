import { ATLAS_API_BASE_URL } from '@/api/config'
import type { components, paths } from '@/api/atlas-openapi'

export type AtlasApiRoute = {
  [Path in keyof paths]: Exclude<paths[Path]['get'], undefined> extends never
    ? never
    : Path
}[keyof paths]

type OperationFor<Path extends AtlasApiRoute> = Exclude<
  paths[Path]['get'],
  undefined
>
type PostOperationFor<Path extends keyof paths> = paths[Path]['post']
type DeleteOperationFor<Path extends keyof paths> = paths[Path]['delete']

type JsonResponse<Operation> = Operation extends {
  responses: {
    200: {
      content: {
        'application/json': infer Response
      }
    }
  }
}
  ? Response
  : never

type JsonRequest<Operation> = Operation extends {
  requestBody: {
    content: {
      'application/json': infer Request
    }
  }
}
  ? Request
  : never

type OperationParameters<Path extends AtlasApiRoute> =
  OperationFor<Path> extends { parameters: infer Parameters }
    ? Parameters
    : never

type PathParameters<Path extends AtlasApiRoute> =
  OperationParameters<Path> extends { path?: infer Parameters }
    ? Exclude<Parameters, undefined>
    : never

type QueryParameters<Path extends AtlasApiRoute> =
  OperationParameters<Path> extends { query?: infer Parameters }
    ? Exclude<Parameters, undefined>
    : never

type AtlasRequestOptions<Path extends AtlasApiRoute> =
  (PathParameters<Path> extends never
    ? { path?: never }
    : { path: PathParameters<Path> }) &
    (QueryParameters<Path> extends never
      ? { query?: never }
      : { query?: QueryParameters<Path> })

type RequestArguments<Path extends AtlasApiRoute> =
  PathParameters<Path> extends never
    ? QueryParameters<Path> extends never
      ? [options?: AtlasRequestOptions<Path>]
      : [options: AtlasRequestOptions<Path>]
    : [options: AtlasRequestOptions<Path>]

export type AtlasRouteResponse<Path extends AtlasApiRoute> = JsonResponse<
  OperationFor<Path>
>
export type AtlasSessionState = JsonResponse<
  OperationFor<'/api/v1/session'>
>
export type AtlasSessionLoginRequest = JsonRequest<
  PostOperationFor<'/api/v1/session'>
>
export type AtlasSessionLoginResponse = JsonResponse<
  PostOperationFor<'/api/v1/session'>
>
export type AtlasPromoteLessonRequest = JsonRequest<
  PostOperationFor<'/api/v1/lessons/{lesson_id}/promote'>
>
export type AtlasRejectLessonRequest = JsonRequest<
  PostOperationFor<'/api/v1/lessons/{lesson_id}/reject'>
>
export type AtlasLessonDispositionResponse = JsonResponse<
  PostOperationFor<'/api/v1/lessons/{lesson_id}/promote'>
>
export type AtlasCreateAcceptanceSessionRequest = JsonRequest<
  PostOperationFor<'/api/v1/reviews/{pr_number}/acceptance-sessions'>
>
export type AtlasAcceptanceConfirmationRequest = JsonRequest<
  PostOperationFor<'/api/v1/acceptance-sessions/{session_id}/confirm'>
>
export type AtlasAcceptanceEvidenceRequest = JsonRequest<
  PostOperationFor<'/api/v1/acceptance-sessions/{session_id}/evidence'>
>
export type AtlasAcceptanceVerificationRequest = JsonRequest<
  PostOperationFor<'/api/v1/acceptance-sessions/{session_id}/verify'>
>
export type AtlasAcceptanceCreationResponse = JsonResponse<
  PostOperationFor<'/api/v1/reviews/{pr_number}/acceptance-sessions'>
>
export type AtlasAcceptanceActionResponse = JsonResponse<
  PostOperationFor<'/api/v1/acceptance-sessions/{session_id}/evidence'>
>
export type AtlasAcceptanceReadResponse = AtlasRouteResponse<'/api/v1/acceptance-sessions/{session_id}'>

export type AtlasValidationError = components['schemas']['HTTPValidationError']

export class AtlasRequestError extends Error {
  readonly name = 'AtlasRequestError'
  readonly status: number
  readonly body: unknown

  constructor({
    body,
    status,
    statusText,
  }: {
    body: unknown
    status: number
    statusText: string
  }) {
    super(`Atlas API request failed with ${status} ${statusText}`.trim())
    this.status = status
    this.body = body
  }
}

export class AtlasApiUnreachableError extends Error {
  readonly name = 'AtlasApiUnreachableError'
  readonly apiBaseUrl: string
  readonly originalError: unknown

  constructor(apiBaseUrl: string, cause?: unknown) {
    super(`Atlas API is unreachable at ${apiBaseUrl}`)
    this.apiBaseUrl = apiBaseUrl
    this.originalError = cause
  }
}

export type AtlasQueryError = AtlasRequestError | AtlasApiUnreachableError

export function isApiUnreachableError(
  error: unknown
): error is AtlasApiUnreachableError {
  return error instanceof AtlasApiUnreachableError
}

function encodePath<Path extends AtlasApiRoute>(
  route: Path,
  options: AtlasRequestOptions<Path> | undefined
): string {
  let encoded = String(route)
  const pathParameters = (options?.path ?? {}) as Record<
    string,
    string | number | boolean
  >

  for (const [name, value] of Object.entries(pathParameters)) {
    encoded = encoded.replace(`{${name}}`, encodeURIComponent(String(value)))
  }

  return encoded
}

function appendQuery<Path extends AtlasApiRoute>(
  url: URL,
  options: AtlasRequestOptions<Path> | undefined
): void {
  const queryParameters = (options?.query ?? {}) as Record<string, unknown>

  for (const [name, value] of Object.entries(queryParameters)) {
    if (value === undefined || value === null) {
      continue
    }
    url.searchParams.set(name, String(value))
  }
}

function requestUrl<Path extends AtlasApiRoute>(
  route: Path,
  options: AtlasRequestOptions<Path> | undefined
): string {
  const url = new URL(encodePath(route, options), window.location.origin)
  appendQuery(url, options)
  return `${url.pathname}${url.search}`
}

function looksLikeProxyFailure(status: number, body: unknown): boolean {
  if (![500, 502, 503, 504].includes(status)) {
    return false
  }

  if (typeof body !== 'string') {
    return false
  }

  const lowerBody = body.toLowerCase()
  return (
    lowerBody.trim() === '' ||
    lowerBody.includes('proxy') ||
    lowerBody.includes('econnrefused') ||
    lowerBody.includes('socket hang up')
  )
}

async function responseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    return response.json()
  }
  return response.text()
}

let atlasCsrfToken: string | null = null

async function atlasJsonRequest<ResponseBody>({
  body,
  includeCsrf,
  idempotencyKey,
  method,
  requestPath,
}: {
  body?: unknown
  includeCsrf: boolean
  idempotencyKey?: string
  method: 'DELETE' | 'POST'
  requestPath: string
}): Promise<ResponseBody> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    'Content-Type': 'application/json',
  }
  if (includeCsrf && atlasCsrfToken) {
    headers['X-Atlas-CSRF'] = atlasCsrfToken
  }
  if (idempotencyKey) {
    headers['Idempotency-Key'] = idempotencyKey
  }

  let response: Response
  try {
    response = await fetch(requestPath, {
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
      headers,
      method,
    })
  } catch (error) {
    throw new AtlasApiUnreachableError(ATLAS_API_BASE_URL, error)
  }

  if (!response.ok) {
    const errorBody = await responseBody(response)
    if (looksLikeProxyFailure(response.status, errorBody)) {
      throw new AtlasApiUnreachableError(ATLAS_API_BASE_URL, errorBody)
    }
    throw new AtlasRequestError({
      body: errorBody,
      status: response.status,
      statusText: response.statusText,
    })
  }

  return response.json() as Promise<ResponseBody>
}

export async function atlasGet<Path extends AtlasApiRoute>(
  route: Path,
  ...[options]: RequestArguments<Path>
): Promise<AtlasRouteResponse<Path>> {
  let response: Response

  try {
    response = await fetch(requestUrl(route, options), {
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
      },
    })
  } catch (error) {
    throw new AtlasApiUnreachableError(ATLAS_API_BASE_URL, error)
  }

  if (!response.ok) {
    const body = await responseBody(response)
    if (looksLikeProxyFailure(response.status, body)) {
      throw new AtlasApiUnreachableError(ATLAS_API_BASE_URL, body)
    }
    throw new AtlasRequestError({
      body,
      status: response.status,
      statusText: response.statusText,
    })
  }

  return response.json() as Promise<AtlasRouteResponse<Path>>
}

export async function atlasLogin(
  request: AtlasSessionLoginRequest
): Promise<AtlasSessionLoginResponse> {
  const response = await atlasJsonRequest<AtlasSessionLoginResponse>({
    body: request,
    includeCsrf: false,
    method: 'POST',
    requestPath: '/api/v1/session',
  })
  atlasCsrfToken = response.csrf_token
  return response
}

export async function atlasSessionState(): Promise<AtlasSessionState> {
  return atlasGet('/api/v1/session')
}

export async function atlasLogout(): Promise<AtlasSessionState> {
  try {
    return await atlasJsonRequest<
      JsonResponse<DeleteOperationFor<'/api/v1/session'>>
    >({
      includeCsrf: true,
      method: 'DELETE',
      requestPath: '/api/v1/session',
    })
  } finally {
    atlasForgetSession()
  }
}

export function atlasForgetSession(): void {
  atlasCsrfToken = null
}

export async function atlasPromoteLesson({
  idempotencyKey,
  lessonId,
  request,
}: {
  idempotencyKey: string
  lessonId: string
  request: AtlasPromoteLessonRequest
}): Promise<AtlasLessonDispositionResponse> {
  return atlasJsonRequest<AtlasLessonDispositionResponse>({
    body: request,
    idempotencyKey,
    includeCsrf: true,
    method: 'POST',
    requestPath: `/api/v1/lessons/${encodeURIComponent(lessonId)}/promote`,
  })
}

export async function atlasRejectLesson({
  idempotencyKey,
  lessonId,
  request,
}: {
  idempotencyKey: string
  lessonId: string
  request: AtlasRejectLessonRequest
}): Promise<AtlasLessonDispositionResponse> {
  return atlasJsonRequest<AtlasLessonDispositionResponse>({
    body: request,
    idempotencyKey,
    includeCsrf: true,
    method: 'POST',
    requestPath: `/api/v1/lessons/${encodeURIComponent(lessonId)}/reject`,
  })
}

export async function atlasCreateAcceptanceSession({
  idempotencyKey,
  prNumber,
  request,
}: {
  idempotencyKey: string
  prNumber: number
  request: AtlasCreateAcceptanceSessionRequest
}): Promise<AtlasAcceptanceCreationResponse> {
  return atlasJsonRequest<AtlasAcceptanceCreationResponse>({
    body: request,
    idempotencyKey,
    includeCsrf: true,
    method: 'POST',
    requestPath: `/api/v1/reviews/${encodeURIComponent(String(prNumber))}/acceptance-sessions`,
  })
}

type AcceptanceStepArguments<Request> = {
  idempotencyKey: string
  request: Request
  sessionId: string
}

export async function atlasPullAcceptanceEvidence({
  idempotencyKey,
  request,
  sessionId,
}: AcceptanceStepArguments<AtlasAcceptanceEvidenceRequest>): Promise<AtlasAcceptanceActionResponse> {
  return atlasJsonRequest<AtlasAcceptanceActionResponse>({
    body: request,
    idempotencyKey,
    includeCsrf: true,
    method: 'POST',
    requestPath: `/api/v1/acceptance-sessions/${encodeURIComponent(sessionId)}/evidence`,
  })
}

export async function atlasConfirmAcceptanceSession({
  idempotencyKey,
  request,
  sessionId,
}: AcceptanceStepArguments<AtlasAcceptanceConfirmationRequest>): Promise<AtlasAcceptanceActionResponse> {
  return atlasJsonRequest<AtlasAcceptanceActionResponse>({
    body: request,
    idempotencyKey,
    includeCsrf: true,
    method: 'POST',
    requestPath: `/api/v1/acceptance-sessions/${encodeURIComponent(sessionId)}/confirm`,
  })
}

export async function atlasVerifyAcceptanceSession({
  idempotencyKey,
  request,
  sessionId,
}: AcceptanceStepArguments<AtlasAcceptanceVerificationRequest>): Promise<AtlasAcceptanceActionResponse> {
  return atlasJsonRequest<AtlasAcceptanceActionResponse>({
    body: request,
    idempotencyKey,
    includeCsrf: true,
    method: 'POST',
    requestPath: `/api/v1/acceptance-sessions/${encodeURIComponent(sessionId)}/verify`,
  })
}
