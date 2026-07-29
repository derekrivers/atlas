import { expect, test, type APIRequestContext } from '@playwright/test'
import {
  assertLessonsResponse,
  assertReviewQueueResponse,
  assertTicketBoardResponse,
  assertTicketDependenciesResponse,
  assertTicketEvidenceResponse,
  liveApiShapeAssertions,
} from './live-api-shape'
import { startAtlasApiServer } from './atlas-api-server'

const apiBaseURL =
  process.env.ATLAS_OPERATOR_E2E_API_URL ?? 'http://127.0.0.1:18000'

let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined

test.beforeAll(async () => {
  apiServer = await startAtlasApiServer()
})

test.afterAll(async () => {
  await apiServer?.stop()
  apiServer = undefined
})

async function getJson(request: APIRequestContext, path: string): Promise<unknown> {
  const response = await request.get(`${apiBaseURL}${path}`)
  expect(response.ok(), `${path} should return 2xx`).toBe(true)
  return response.json()
}

test('seeded live API exposes the operator edge-case data shapes', async ({
  request,
}) => {
  const board = await getJson(request, '/api/v1/tickets')
  assertTicketBoardResponse(board)
  const boardKeys = board.tickets.map((ticket) => ticket.key)
  const terminalTickets = board.tickets.filter((ticket) =>
    ['done', 'rejected'].includes(ticket.status)
  )

  expect(board.tickets).toHaveLength(17)
  expect(terminalTickets).toHaveLength(16)
  expect(terminalTickets.length / board.tickets.length).toBeGreaterThan(0.9)
  expect(boardKeys.indexOf('ATLAS-10')).toBeLessThan(
    boardKeys.indexOf('ATLAS-2')
  )

  const reviews = await getJson(request, '/api/v1/reviews')
  assertReviewQueueResponse(reviews)
  expect(reviews.reviews).toEqual([])

  const noEvidence = await getJson(request, '/api/v1/tickets/ATLAS-10/evidence')
  assertTicketEvidenceResponse(noEvidence)
  expect(noEvidence.evidence).toEqual([])

  const evidence = await getJson(request, '/api/v1/tickets/ATLAS-1/evidence')
  assertTicketEvidenceResponse(evidence)
  expect(evidence.evidence.map((record) => record.type)).toEqual([
    'manual_approval',
    'test_result',
  ])
  expect(evidence.evidence.map((record) => record.tier)).toEqual([
    'agent',
    'system',
  ])
  expect(evidence.evidence.map((record) => record.has_system_pin_triple)).toEqual([
    false,
    true,
  ])

  const dependencies = await getJson(
    request,
    '/api/v1/tickets/ATLAS-2/dependencies'
  )
  assertTicketDependenciesResponse(dependencies)
  expect(dependencies.readiness.ready).toBe(false)
  expect(dependencies.readiness.reasons.map((reason) => reason.code)).toEqual(
    expect.arrayContaining([
      'wrong_status',
      'adr_not_accepted',
      'no_acceptance_criteria',
    ])
  )
  expect(dependencies.readiness.reasons.length).toBeGreaterThan(1)

  const lessons = await getJson(request, '/api/v1/lessons')
  assertLessonsResponse(lessons)
  expect(lessons.lessons).toHaveLength(1)
  expect(lessons.lessons[0].source_ticket_id).toMatch(
    /^[0-9a-f-]{36}$/i
  )
  expect(lessons.lessons[0].source_ticket_id).not.toMatch(/^ATLAS-\d+$/)
  expect(lessons.lessons[0].related_ticket_ids).toHaveLength(1)
})

test('all ratified v1 routes return the expected runtime shape from the live seed', async ({
  request,
}) => {
  const routeRequests = [
    ['/api/v1/tickets', '/api/v1/tickets'],
    ['/api/v1/tickets/count', '/api/v1/tickets/count'],
    ['/api/v1/tickets/{key}', '/api/v1/tickets/ATLAS-1'],
    ['/api/v1/tickets/{key}/evidence', '/api/v1/tickets/ATLAS-1/evidence'],
    [
      '/api/v1/tickets/{key}/dependencies',
      '/api/v1/tickets/ATLAS-2/dependencies',
    ],
    ['/api/v1/epics', '/api/v1/epics'],
    ['/api/v1/lessons', '/api/v1/lessons'],
    [
      '/api/v1/dependencies/critical-path',
      '/api/v1/dependencies/critical-path',
    ],
    ['/api/v1/dependencies/graph', '/api/v1/dependencies/graph'],
    ['/api/v1/reviews', '/api/v1/reviews'],
    ['/api/v1/status', '/api/v1/status'],
  ] as const

  for (const [route, path] of routeRequests) {
    const payload = await getJson(request, path)
    liveApiShapeAssertions[route](payload)
  }
})

test('runtime live API schema assertions reject a seeded unexpected shape', async ({
  request,
}) => {
  const board = await getJson(request, '/api/v1/tickets')
  assertTicketBoardResponse(board)

  const divergentBoard = structuredClone(board) as typeof board & {
    tickets: Array<(typeof board.tickets)[number] & { seeded_contract_probe?: true }>
  }
  divergentBoard.tickets[0].seeded_contract_probe = true

  expect(() => assertTicketBoardResponse(divergentBoard)).toThrow(
    /seeded_contract_probe/
  )
})
