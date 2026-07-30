import { expect, test, type APIRequestContext } from '@playwright/test'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ticketDetailHref } from '../../src/app-shell/surfaces'
import type { AtlasRouteResponse } from '../../src/api/client'
import { assertTicketDependenciesResponse } from './live-api-shape'
import { startAtlasApiServer } from './atlas-api-server'

type TicketDependencies =
  AtlasRouteResponse<'/api/v1/tickets/{key}/dependencies'>

const apiBaseURL =
  process.env.ATLAS_OPERATOR_E2E_API_URL ?? 'http://127.0.0.1:18000'
const seedPath = join(
  dirname(fileURLToPath(import.meta.url)),
  'fixtures',
  'ticket-detail-dependencies-seed.json'
)
const danglingTarget = '00000000-0000-4000-8000-00000000d393'

let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined

test.beforeAll(async () => {
  apiServer = await startAtlasApiServer({ seedPath })
})

test.afterAll(async () => {
  await apiServer?.stop()
  apiServer = undefined
})

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

test('renders the full not-ready verdict with targets, statuses, and dangling defects', async ({
  page,
  request,
}) => {
  const dependencies = await getTicketDependencies(request, 'ATLAS-2')
  expect(dependencies.readiness.ready).toBe(false)
  expect(dependencies.readiness.reasons.map((reason) => reason.code)).toEqual([
    'wrong_status',
    'dependency_not_done',
    'adr_not_accepted',
    'dangling_target',
    'no_acceptance_criteria',
  ])

  await page.goto(ticketDetailHref('ATLAS-2'))
  await page.getByRole('tab', { name: 'Dependencies' }).click()

  const panel = page.getByTestId('ticket-detail-dependencies-panel')
  await expect(page.getByTestId('ticket-detail-readiness-verdict')).toHaveText(
    'Not ready'
  )
  await expect(page.getByTestId('ticket-detail-readiness-reason')).toHaveCount(
    dependencies.readiness.reasons.length
  )

  for (const reason of dependencies.readiness.reasons) {
    await expect(panel).toContainText(reason.code)
    if (reason.target) {
      await expect(panel).toContainText(reason.target)
    }
    if (reason.status) {
      await expect(panel).toContainText(reason.status)
    }
  }

  await expect(panel).toContainText('Wrong status')
  await expect(panel).toContainText('Dependency not done')
  await expect(panel).toContainText('ADR not accepted')
  await expect(panel).toContainText('No acceptance criteria')
  await expect(panel).toContainText(danglingTarget)
  await expect(page.getByTestId('ticket-detail-dependency-defect')).toBeVisible()
  await expect(page.getByTestId('ticket-detail-blocker-defect')).toBeVisible()
  await expect(
    panel.getByRole('button', {
      name: /dispatch|move|status change|start|complete/i,
    })
  ).toHaveCount(0)
})

test('walks one blocker link and one blocked-by link from the ticket tab', async ({
  page,
}) => {
  await page.goto(ticketDetailHref('ATLAS-2'))
  await page.getByRole('tab', { name: 'Dependencies' }).click()

  await page.getByTestId('ticket-detail-blocker-link').click()
  await expect(page).toHaveURL(/\/tickets\/ATLAS-3$/)
  await expect(page.getByTestId('ticket-detail-key')).toHaveText('ATLAS-3')

  await page.goto(ticketDetailHref('ATLAS-2'))
  await page.getByRole('tab', { name: 'Dependencies' }).click()

  await page.getByTestId('ticket-detail-blocked-by-link').click()
  await expect(page).toHaveURL(/\/tickets\/ATLAS-4$/)
  await expect(page.getByTestId('ticket-detail-key')).toHaveText('ATLAS-4')
})

test('renders a ready ticket verdict with no reason list', async ({
  page,
  request,
}) => {
  const dependencies = await getTicketDependencies(request, 'ATLAS-1')
  expect(dependencies.readiness).toEqual({ ready: true, reasons: [] })

  await page.goto(ticketDetailHref('ATLAS-1'))
  await page.getByRole('tab', { name: 'Dependencies' }).click()

  await expect(page.getByTestId('ticket-detail-readiness-verdict')).toHaveText(
    'Ready'
  )
  await expect(
    page.getByTestId('ticket-detail-readiness-reasons')
  ).toHaveCount(0)
})
