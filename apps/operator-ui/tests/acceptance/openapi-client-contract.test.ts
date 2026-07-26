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

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const repoRoot = join(appRoot, '..', '..')
const generatedClientPath = join(appRoot, 'src', 'api', 'atlas-openapi.ts')

const expectedV1Routes = [
  '/api/v1/tickets',
  '/api/v1/tickets/count',
  '/api/v1/tickets/{key}',
  '/api/v1/tickets/{key}/evidence',
  '/api/v1/tickets/{key}/dependencies',
  '/api/v1/lessons',
  '/api/v1/dependencies/critical-path',
  '/api/v1/reviews',
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
type RouteResponse<Path extends keyof paths> = JsonResponse<GetOperation<Path>>

type TicketBoardItem = RouteResponse<'/api/v1/tickets'>['tickets'][number]
type TicketDetail = RouteResponse<'/api/v1/tickets/{key}'>
type TicketEvidenceItem =
  RouteResponse<'/api/v1/tickets/{key}/evidence'>['evidence'][number]
type TicketDependencies = RouteResponse<'/api/v1/tickets/{key}/dependencies'>
type LessonItem = RouteResponse<'/api/v1/lessons'>['lessons'][number]
type ReviewItem = RouteResponse<'/api/v1/reviews'>['reviews'][number]

const routeTypeParity: Assert<Equal<keyof paths, ExpectedV1Route>> = true
const closedValueFieldParity: [
  Assert<Equal<TicketBoardItem['status'], Schema['TicketStatus']>>,
  Assert<Equal<TicketBoardItem['ticket_type'], Schema['TicketType']>>,
  Assert<Equal<TicketBoardItem['risk_level'], Schema['RiskLevel']>>,
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
    expect(expectedV1Routes).toHaveLength(9)
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
})
