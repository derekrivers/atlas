import { spawnSync } from 'node:child_process'
import {
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import type { components, paths } from '@/api/atlas-openapi'
import { atlasOpenApiEnums } from '@/api/atlas-openapi-runtime'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const repoRoot = join(appRoot, '..', '..')
const generatedClientPath = join(appRoot, 'src', 'api', 'atlas-openapi.ts')

const expectedV1Routes = [
  '/api/v1/delivery-control',
  '/api/v1/delivery-control/policy',
  '/api/v1/tickets',
  '/api/v1/tickets/count',
  '/api/v1/tickets/{key}',
  '/api/v1/tickets/{key}/evidence',
  '/api/v1/tickets/{key}/dependencies',
  '/api/v1/epics',
  '/api/v1/lessons',
  '/api/v1/lessons/{lesson_id}/promote',
  '/api/v1/lessons/{lesson_id}/reject',
  '/api/v1/dependencies/critical-path',
  '/api/v1/dependencies/graph',
  '/api/v1/reviews',
  '/api/v1/reviews/{pr_number}/acceptance-sessions',
  '/api/v1/acceptance-sessions/{session_id}',
  '/api/v1/acceptance-sessions/{session_id}/evidence',
  '/api/v1/acceptance-sessions/{session_id}/confirm',
  '/api/v1/acceptance-sessions/{session_id}/verify',
  '/api/v1/session',
  '/api/v1/status',
] as const satisfies readonly (keyof paths)[]

type ExpectedV1Route = (typeof expectedV1Routes)[number]
type Equal<Left, Right> = (<Value>() => Value extends Left ? 1 : 2) extends <
  Value,
>() => Value extends Right ? 1 : 2
  ? true
  : false
type Assert<Type extends true> = Type
type Schema = components['schemas']
type GetOperation<Path extends keyof paths> = paths[Path]['get']
type PostOperation<Path extends keyof paths> = paths[Path]['post']
type DeleteOperation<Path extends keyof paths> = paths[Path]['delete']
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
type RouteResponse<Path extends keyof paths> = JsonResponse<GetOperation<Path>>

type TicketBoardItem = RouteResponse<'/api/v1/tickets'>['tickets'][number]
type DeliveryControl = RouteResponse<'/api/v1/delivery-control'>
type PolicyRequest = JsonRequest<
  PostOperation<'/api/v1/delivery-control/policy'>
>
type PolicyResponse = JsonResponse<
  PostOperation<'/api/v1/delivery-control/policy'>
>
type TicketDetail = RouteResponse<'/api/v1/tickets/{key}'>
type TicketEvidenceItem =
  RouteResponse<'/api/v1/tickets/{key}/evidence'>['evidence'][number]
type TicketDependencies = RouteResponse<'/api/v1/tickets/{key}/dependencies'>
type DependencyGraph = RouteResponse<'/api/v1/dependencies/graph'>
type EpicItem = RouteResponse<'/api/v1/epics'>['epics'][number]
type LessonItem = RouteResponse<'/api/v1/lessons'>['lessons'][number]
type PromoteLessonRequest = JsonRequest<
  PostOperation<'/api/v1/lessons/{lesson_id}/promote'>
>
type PromoteLessonResponse = JsonResponse<
  PostOperation<'/api/v1/lessons/{lesson_id}/promote'>
>
type RejectLessonRequest = JsonRequest<
  PostOperation<'/api/v1/lessons/{lesson_id}/reject'>
>
type RejectLessonResponse = JsonResponse<
  PostOperation<'/api/v1/lessons/{lesson_id}/reject'>
>
type LessonActionReceipt = PromoteLessonResponse['receipt']
type AcceptanceSessionRead = RouteResponse<
  '/api/v1/acceptance-sessions/{session_id}'
>
type AcceptanceSessionCreateRequest = JsonRequest<
  PostOperation<'/api/v1/reviews/{pr_number}/acceptance-sessions'>
>
type AcceptanceConfirmationRequest = JsonRequest<
  PostOperation<'/api/v1/acceptance-sessions/{session_id}/confirm'>
>
type AcceptanceActionResponse = JsonResponse<
  PostOperation<'/api/v1/acceptance-sessions/{session_id}/verify'>
>
type ReviewItem = RouteResponse<'/api/v1/reviews'>['reviews'][number]
type SessionState = RouteResponse<'/api/v1/session'>
type SessionLoginRequest = JsonRequest<PostOperation<'/api/v1/session'>>
type SessionLoginResponse = JsonResponse<PostOperation<'/api/v1/session'>>
type SessionLogoutResponse = JsonResponse<DeleteOperation<'/api/v1/session'>>

const routeTypeParity: Assert<Equal<keyof paths, ExpectedV1Route>> = true
const lessonCommandTypeParity: [
  Assert<Equal<PromoteLessonRequest, Schema['PromoteLessonRequest']>>,
  Assert<Equal<RejectLessonRequest, Schema['RejectLessonRequest']>>,
  Assert<Equal<PromoteLessonResponse, Schema['LessonDispositionResponse']>>,
  Assert<Equal<RejectLessonResponse, Schema['LessonDispositionResponse']>>,
  Assert<Equal<PromoteLessonResponse['lesson'], Schema['LessonItemSchema']>>,
  Assert<
    Equal<LessonActionReceipt['actor']['type'], Extract<Schema['ActorType'], 'human'>>
  >,
  Assert<
    Equal<LessonActionReceipt['before_status'], Schema['EntityStatus'] | null>
  >,
  Assert<Equal<LessonActionReceipt['after_status'], Schema['EntityStatus'] | null>>,
] = [true, true, true, true, true, true, true, true]
const acceptanceSessionTypeParity: [
  Assert<
    Equal<AcceptanceSessionRead, Schema['AcceptanceSessionReadResponse']>
  >,
  Assert<
    Equal<
      AcceptanceSessionCreateRequest,
      Schema['CreateAcceptanceSessionRequest']
    >
  >,
  Assert<
    Equal<
      AcceptanceConfirmationRequest,
      Schema['AcceptanceConfirmationRequestSchema']
    >
  >,
  Assert<
    Equal<AcceptanceActionResponse, Schema['AcceptanceSessionActionResponse']>
  >,
  Assert<
    Equal<
      AcceptanceSessionRead['reasons'][number],
      Schema['AcceptanceSessionBlockingReason']
    >
  >,
  Assert<
    Equal<
      AcceptanceSessionRead['session']['lifecycle'],
      Schema['AcceptanceSessionLifecycle']
    >
  >,
] = [true, true, true, true, true, true]
const deliveryControlTypeParity: [
  Assert<Equal<DeliveryControl, Schema['DeliveryControlResponse']>>,
  Assert<Equal<PolicyRequest, Schema['DeliveryAdmissionPolicyRequest']>>,
  Assert<Equal<PolicyResponse, Schema['DeliveryAdmissionPolicyResponse']>>,
  Assert<
    Equal<
      PolicyRequest['risk_lane_limits'][number],
      Schema['DeliveryAdmissionRiskLaneLimitRequest']
    >
  >,
  Assert<
    Equal<
      PolicyRequest['component_lane_limits'][number],
      Schema['DeliveryAdmissionComponentLaneLimitRequest']
    >
  >,
  Assert<
    Equal<
      DeliveryControl['policy']['mode'],
      Schema['DeliveryAdmissionMode']
    >
  >,
  Assert<
    Equal<
      DeliveryControl['occupancy']['over_capacity_reasons'][number]['dimension'],
      Schema['OccupancyDimension']
    >
  >,
  Assert<
    Equal<
      NonNullable<
        DeliveryControl['latest_admission']
      >['decisions'][number]['reasons'][number]['code'],
      Schema['AdmissionHoldCode']
    >
  >,
  Assert<
    Equal<
      NonNullable<
        DeliveryControl['latest_admission']
      >['decisions'][number]['rank_inputs'],
      Schema['DeliveryControlRankInputsSchema']
    >
  >,
  Assert<
    Equal<
      DeliveryControl['indeterminate_reasons'][number]['reason'],
      Schema['AdmissionSyncReason']
    >
  >,
] = [true, true, true, true, true, true, true, true, true, true]
const closedValueFieldParity: [
  Assert<Equal<TicketBoardItem['status'], Schema['TicketStatus']>>,
  Assert<Equal<TicketBoardItem['ticket_type'], Schema['TicketType']>>,
  Assert<Equal<TicketBoardItem['risk_level'], Schema['RiskLevel']>>,
  Assert<Equal<TicketBoardItem['epic_key'], string | null>>,
  Assert<Equal<TicketDetail['status'], Schema['TicketStatus']>>,
  Assert<Equal<TicketDetail['ticket_type'], Schema['TicketType']>>,
  Assert<Equal<TicketDetail['risk_level'], Schema['RiskLevel']>>,
  Assert<Equal<TicketEvidenceItem['type'], Schema['EvidenceType']>>,
  Assert<Equal<TicketEvidenceItem['tier'], Schema['ActorType']>>,
  Assert<Equal<TicketEvidenceItem['status'], Schema['EvidenceStatus']>>,
  Assert<
    Equal<TicketDependencies['blockers'][number]['code'], Schema['NotReadyCode']>
  >,
  Assert<
    Equal<
      TicketDependencies['readiness']['reasons'][number]['code'],
      Schema['NotReadyCode']
    >
  >,
  Assert<
    Equal<
      DependencyGraph['edges'][number]['dependency_type'],
      Schema['DependencyType']
    >
  >,
  Assert<Equal<EpicItem['status'], Schema['EpicStatus']>>,
  Assert<Equal<EpicItem['risk_level'], Schema['RiskLevel']>>,
  Assert<Equal<EpicItem['created_by_type'], Schema['ActorType']>>,
  Assert<Equal<LessonItem['status'], Schema['EntityStatus']>>,
  Assert<Equal<LessonItem['category'], Schema['LessonCategory']>>,
  Assert<Equal<LessonItem['created_by_type'], Schema['ActorType']>>,
  Assert<Equal<ReviewItem['status'], Schema['TicketStatus']>>,
  Assert<Equal<ReviewItem['ticket_type'], Schema['TicketType']>>,
  Assert<Equal<ReviewItem['verdict'], Schema['EvidenceStatus']>>,
  Assert<
    Equal<ReviewItem['checks'][number]['check_type'], Schema['VerificationCheckType']>
  >,
  Assert<Equal<ReviewItem['checks'][number]['status'], Schema['EvidenceStatus']>>,
  Assert<Equal<SessionState, Schema['SessionStateResponse']>>,
  Assert<Equal<SessionLoginRequest, Schema['SessionLoginRequest']>>,
  Assert<Equal<SessionLoginResponse, Schema['SessionLoginResponse']>>,
  Assert<Equal<SessionLogoutResponse, Schema['SessionStateResponse']>>,
] = [
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
  true,
  true,
]

function tsFiles(root: string): string[] {
  return readdirSync(root).flatMap((entry) => {
    const path = join(root, entry)
    if (entry === 'node_modules' || entry === 'dist') {
      return []
    }
    if (statSync(path).isDirectory()) {
      return tsFiles(path)
    }
    return path.endsWith('.ts') || path.endsWith('.tsx') ? [path] : []
  })
}

describe('generated OpenAPI TypeScript client contract', () => {
  it('represents every current v1 route in the generated types', () => {
    expect(routeTypeParity).toBe(true)
    expect(expectedV1Routes).toHaveLength(21)
  })

  it('keeps lesson command request, response, actor and status types generated', () => {
    expect(lessonCommandTypeParity.every((value) => value)).toBe(true)
  })

  it('keeps acceptance-session requests, all reasons and receipts generated', () => {
    expect(acceptanceSessionTypeParity.every((value) => value)).toBe(true)
    expect(atlasOpenApiEnums.AcceptanceSessionBlockingReason).toContain(
      'external_read_timeout'
    )
    expect(atlasOpenApiEnums.AcceptanceSessionLifecycle).toContain('merge_ready')
  })

  it('keeps delivery-control read and complete policy types generated', () => {
    expect(deliveryControlTypeParity.every((value) => value)).toBe(true)
    expect(atlasOpenApiEnums.AdmissionHoldCode).toContain('review_budget')
    expect(atlasOpenApiEnums.AdmissionHoldCode).toContain('integration_budget')
    expect(atlasOpenApiEnums.AdmissionSyncReason).toContain('write_indeterminate')
  })

  it('types closed-value response fields through generated schema enum members', () => {
    expect(closedValueFieldParity.every((value) => value)).toBe(true)

    const enumDeclaration =
      /^\s*(?:export\s+)?(?:const\s+)?enum\s+[A-Za-z_$]/mu
    for (const root of ['src', 'tests']) {
      for (const path of tsFiles(join(appRoot, root))) {
        expect(
          readFileSync(path, 'utf8'),
          `${relative(appRoot, path)} must not declare a hand-authored TypeScript enum`
        ).not.toMatch(enumDeclaration)
      }
    }
  })

  it('keeps session credentials out of browser storage helpers', () => {
    const clientSource = readFileSync(generatedClientPath.replace(
      'atlas-openapi.ts',
      'client.ts'
    ), 'utf8')

    expect(clientSource).toContain('let atlasCsrfToken: string | null = null')
    expect(clientSource).not.toContain('localStorage')
    expect(clientSource).not.toContain('sessionStorage')
    expect(clientSource).not.toContain('document.cookie')
  })

  it('publishes runtime enum metadata for view matrices', () => {
    const checkTypes: readonly Schema['VerificationCheckType'][] =
      atlasOpenApiEnums.VerificationCheckType

    expect(checkTypes).toHaveLength(7)
    expect(new Set(checkTypes).size).toBe(checkTypes.length)
  })

  it(
    'fails the drift guard for a seeded Python-side response field addition',
    () => {
      const tempDir = mkdtempSync(join(tmpdir(), 'atlas-openapi-drift-'))
      try {
        const seededClient = join(tempDir, 'atlas-openapi.ts')
        const result = spawnSync(
          'uv',
          [
            'run',
            'python',
            '-m',
            'atlas.tools.operator_ui_openapi',
            '--output',
            seededClient,
            '--compare-to',
            generatedClientPath,
            '--seed-response-field',
            'TicketCountResponse.seeded_contract_probe',
          ],
          {
            cwd: repoRoot,
            encoding: 'utf8',
            env: {
              ...process.env,
              UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? '/tmp/uv-cache',
              UV_LINK_MODE: process.env.UV_LINK_MODE ?? 'copy',
            },
          }
        )

        expect(result.status).toBe(1)
        expect(result.stderr).toContain(
          'OpenAPI TypeScript client drift detected'
        )
        expect(result.stderr).toContain('seeded_contract_probe')
      } finally {
        rmSync(tempDir, { force: true, recursive: true })
      }
    },
    30_000
  )

  it('rejects check mode with a custom output path', () => {
    const result = spawnSync(
      'uv',
      [
        'run',
        'python',
        '-m',
        'atlas.tools.operator_ui_openapi',
        '--check',
        '--output',
        join(tmpdir(), 'atlas-openapi-invalid-output.ts'),
      ],
      {
        cwd: repoRoot,
        encoding: 'utf8',
        env: {
          ...process.env,
          UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? '/tmp/uv-cache',
          UV_LINK_MODE: process.env.UV_LINK_MODE ?? 'copy',
        },
      }
    )

    expect(result.status).toBe(2)
    expect(result.stderr).toContain('--check cannot be combined with --output')
  })
})
