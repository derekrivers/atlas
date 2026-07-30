import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { ticketDetailHref } from '../../src/app-shell/surfaces'
import type { components } from '../../src/api/atlas-openapi'
import {
  assertTicketDependenciesResponse,
  assertTicketDetailResponse,
  assertTicketEvidenceResponse,
} from './live-api-shape'
import { startAtlasApiServer } from './atlas-api-server'

type TicketDetail = components['schemas']['TicketDetailResponse']
type TicketDependencies = components['schemas']['TicketDependenciesResponse']
type TicketEvidence = components['schemas']['TicketEvidenceResponse']

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

async function getTicketDetail(
  request: APIRequestContext,
  key: string
): Promise<TicketDetail> {
  const response = await request.get(`${apiBaseURL}/api/v1/tickets/${key}`)
  expect(response.ok(), `${key} detail should return 2xx`).toBe(true)
  const body: unknown = await response.json()
  assertTicketDetailResponse(body)
  return body
}

async function getTicketEvidence(
  request: APIRequestContext,
  key: string
): Promise<TicketEvidence> {
  const response = await request.get(`${apiBaseURL}/api/v1/tickets/${key}/evidence`)
  expect(response.ok(), `${key} evidence should return 2xx`).toBe(true)
  const body: unknown = await response.json()
  assertTicketEvidenceResponse(body)
  return body
}

async function getTicketDependencies(
  request: APIRequestContext,
  key: string
): Promise<TicketDependencies> {
  const response = await request.get(
    `${apiBaseURL}/api/v1/tickets/${key}/dependencies`
  )
  expect(response.ok(), `${key} dependencies should return 2xx`).toBe(true)
  const body: unknown = await response.json()
  assertTicketDependenciesResponse(body)
  return body
}

async function expectField(page: Page, testId: string, value: number | string | null) {
  await expect(page.getByTestId(testId)).toHaveText(
    value === null ? 'None' : String(value)
  )
}

async function expectList(page: Page, testId: string, values: string[]) {
  await expect(page.getByTestId(`${testId}-item`)).toHaveText(values)
}

async function selectMetadata(page: Page) {
  await page.getByRole('tab', { name: 'Metadata' }).click()
  await expect(page.getByTestId('ticket-detail-metadata-panel')).toBeVisible()
}

test('renders every ticket detail field from the live API response', async ({
  page,
  request,
}) => {
  const ticket = await getTicketDetail(request, 'ATLAS-1')

  await page.goto(ticketDetailHref(ticket.key))

  for (const tab of ['Definition', 'Metadata', 'Evidence', 'Dependencies']) {
    await expect(page.getByRole('tab', { name: tab })).toBeVisible()
  }

  await expectField(page, 'ticket-detail-key', ticket.key)
  await expectField(page, 'ticket-detail-title', ticket.title)
  await expectField(page, 'ticket-detail-objective', ticket.objective)
  await expectField(page, 'ticket-detail-context', ticket.context)
  await expectList(page, 'ticket-detail-relevant-docs', ticket.relevant_docs)
  await expectList(
    page,
    'ticket-detail-acceptance-criteria',
    ticket.acceptance_criteria
  )
  await expectList(page, 'ticket-detail-non-goals', ticket.non_goals)
  await expectList(
    page,
    'ticket-detail-implementation-notes',
    ticket.implementation_notes
  )
  await expectList(
    page,
    'ticket-detail-test-requirements',
    ticket.test_requirements
  )
  await expectList(
    page,
    'ticket-detail-documentation-requirements',
    ticket.documentation_requirements
  )
  await expectList(
    page,
    'ticket-detail-definition-of-done',
    ticket.definition_of_done
  )

  await selectMetadata(page)

  await expectField(page, 'ticket-detail-status', ticket.status)
  await expectField(page, 'ticket-detail-ticket-type', ticket.ticket_type)
  await expectField(page, 'ticket-detail-risk-level', ticket.risk_level)
  await expectField(page, 'ticket-detail-priority', ticket.priority)
  await expectField(
    page,
    'ticket-detail-estimated-effort',
    ticket.estimated_effort
  )
  await expectField(page, 'ticket-detail-component', ticket.component)
  await expect(page.getByTestId('ticket-detail-tags-item')).toHaveText(ticket.tags)
  await expectField(page, 'ticket-detail-source-anchor', ticket.source_anchor)
  await expectField(
    page,
    'ticket-detail-external-linear-id',
    ticket.external_linear_id
  )
  await expectField(
    page,
    'ticket-detail-external-github-issue-id',
    ticket.external_github_issue_id
  )
  await expectField(page, 'ticket-detail-created-at', ticket.created_at)
  await expectField(page, 'ticket-detail-updated-at', ticket.updated_at)
  await expectField(page, 'ticket-detail-completed-at', ticket.completed_at)

  await page.getByRole('tab', { name: 'Evidence' }).click()
  await expect(page.getByTestId('ticket-detail-evidence-panel')).toBeVisible()

  const dependencies = await getTicketDependencies(request, ticket.key)
  await page.getByRole('tab', { name: 'Dependencies' }).click()
  await expect(page.getByTestId('ticket-detail-dependencies-panel')).toBeVisible()
  await expect(page.getByTestId('ticket-detail-readiness-verdict')).toHaveText(
    dependencies.readiness.ready ? 'Ready' : 'Not ready'
  )
})

test('renders ticket evidence from the live API in oldest-first order', async ({
  page,
  request,
}) => {
  const ticket = await getTicketDetail(request, 'ATLAS-1')
  const evidence = await getTicketEvidence(request, ticket.key)

  expect(evidence.evidence.map((record) => record.type)).toEqual([
    'manual_approval',
    'test_result',
  ])
  expect(evidence.evidence.map((record) => record.tier)).toEqual([
    'agent',
    'system',
  ])
  expect(evidence.evidence.map((record) => record.status)).toEqual([
    'pending',
    'passed',
  ])
  expect(evidence.evidence.map((record) => record.has_system_pin_triple)).toEqual([
    false,
    true,
  ])

  await page.goto(ticketDetailHref(ticket.key))
  await page.getByRole('tab', { name: 'Evidence' }).click()

  const panel = page.getByTestId('ticket-detail-evidence-panel')
  await expect(panel).toBeVisible()
  await expect(page.getByTestId('ticket-evidence-record')).toHaveCount(
    evidence.evidence.length
  )
  await expect(page.getByTestId('ticket-evidence-type')).toHaveText(
    evidence.evidence.map((record) => record.type)
  )
  await expect(page.getByTestId('ticket-evidence-tier')).toHaveText(
    evidence.evidence.map((record) => record.tier)
  )
  await expect(page.getByTestId('ticket-evidence-status')).toHaveText(
    evidence.evidence.map((record) => record.status)
  )
  await expect(page.getByTestId('ticket-evidence-pin-state-label')).toHaveText([
    'System pin triple incomplete',
    'System pin triple complete',
  ])

  const renderedPinStates = await page
    .getByTestId('ticket-evidence-pin-state')
    .evaluateAll((elements) =>
      elements.map((element) => element.getAttribute('data-pin-state'))
    )
  expect(renderedPinStates).toEqual(['incomplete', 'complete'])

  const interactiveElements = await panel
    .locator('a, button, details, input, select, summary, textarea')
    .evaluateAll((elements) =>
      elements.map((element) =>
        [
          element.tagName.toLowerCase(),
          element.getAttribute('aria-label') ?? '',
          element.textContent ?? '',
        ].join(' ')
      )
    )
  expect(interactiveElements).toEqual([])
})

test('renders a seeded ticket with no evidence as an empty state', async ({
  page,
  request,
}) => {
  const ticket = await getTicketDetail(request, 'ATLAS-10')
  const evidence = await getTicketEvidence(request, ticket.key)

  expect(evidence.evidence).toEqual([])

  await page.goto(ticketDetailHref(ticket.key))
  await page.getByRole('tab', { name: 'Evidence' }).click()

  const panel = page.getByTestId('ticket-detail-evidence-panel')
  await expect(panel.getByRole('status')).toContainText('No evidence stored')
  await expect(panel.getByRole('status')).toContainText(
    'This ticket has no stored evidence records.'
  )
  await expect(panel.locator('[role="alert"]')).toHaveCount(0)
})

test('renders unknown ticket keys with the API native 404 body in the shell', async ({
  page,
  request,
}) => {
  const unknownKey = 'ATLAS-999999'
  const apiResponse = await request.get(
    `${apiBaseURL}/api/v1/tickets/${unknownKey}`
  )
  expect(apiResponse.status()).toBe(404)
  const nativeBody = await apiResponse.text()

  await page.goto(ticketDetailHref(unknownKey))

  await expect(page.getByTestId('native-detail-body')).toHaveText(nativeBody)
  await expect(page.getByRole('link', { name: 'Overview' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Search routes/ })).toBeVisible()
})

test('renders null effort, no component, and empty tags without overflow', async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const ticket = await getTicketDetail(request, 'ATLAS-10')

  expect(ticket.estimated_effort).toBeNull()
  expect(ticket.component).toBeNull()
  expect(ticket.tags).toEqual([])

  await page.goto(ticketDetailHref(ticket.key))
  await selectMetadata(page)

  await expectField(page, 'ticket-detail-estimated-effort', null)
  await expectField(page, 'ticket-detail-component', null)
  await expect(page.getByTestId('ticket-detail-tags-empty')).toHaveText('None')

  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth
  )
  expect(hasHorizontalOverflow).toBe(false)
})
