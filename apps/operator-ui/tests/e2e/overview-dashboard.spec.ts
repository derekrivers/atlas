import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import {
  assertReviewQueueResponse,
  assertTicketBoardResponse,
  liveApiShapeAssertions,
  type LiveApiGetRoute,
} from './live-api-shape'
import { startAtlasApiServer } from './atlas-api-server'
import type { paths } from '../../src/api/atlas-openapi'

type GetOperation<Path extends LiveApiGetRoute> = Exclude<
  paths[Path]['get'],
  undefined
>
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
type RouteResponse<Path extends LiveApiGetRoute> = JsonResponse<
  GetOperation<Path>
>

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

async function getJson<Path extends LiveApiGetRoute>(
  request: APIRequestContext,
  path: Path
): Promise<RouteResponse<Path>> {
  const response = await request.get(`${apiBaseURL}${path}`)
  expect(response.ok(), `${path} should return 2xx`).toBe(true)
  const payload = await response.json()
  liveApiShapeAssertions[path](payload)
  return payload
}

async function numericText(page: Page, testId: string): Promise<number> {
  const text = await page.getByTestId(testId).innerText()
  return Number(text.trim())
}

function statusDistributionTotal(
  board: RouteResponse<'/api/v1/tickets'>
): number {
  return board.tickets.reduce((total) => total + 1, 0)
}

test('overview dashboard renders API-exact status, review, and critical-path totals', async ({
  page,
  request,
}) => {
  const status = await getJson(request, '/api/v1/status')
  const board = await getJson(request, '/api/v1/tickets')
  const reviews = await getJson(request, '/api/v1/reviews')
  const criticalPath = await getJson(request, '/api/v1/dependencies/critical-path')
  assertTicketBoardResponse(board)
  assertReviewQueueResponse(reviews)

  await page.goto('/')
  await expect(page.getByTestId('overview-dashboard')).toBeVisible()

  await expect(page.getByTestId('overview-ticket-count')).toHaveText(
    String(status.ticket_count)
  )
  await expect(page.getByTestId('overview-evidence-count')).toHaveText(
    String(status.evidence_count)
  )
  await expect(page.getByTestId('overview-review-depth')).toHaveText(
    String(reviews.reviews.length)
  )
  await expect(page.getByTestId('overview-critical-path-total-effort')).toHaveText(
    String(criticalPath.total_effort)
  )

  const renderedDistributionTotal = Number(
    await page.getByTestId('overview-status-distribution-total').getAttribute('data-total')
  )
  expect(renderedDistributionTotal).toBe(statusDistributionTotal(board))
  expect(renderedDistributionTotal).toBe(status.ticket_count)

  if (criticalPath.steps.length > 0) {
    await expect(page.getByTestId('overview-critical-path-head-link')).toHaveText(
      new RegExp(criticalPath.steps[0].key)
    )
  }

  expect(await numericText(page, 'overview-ticket-count')).toBe(status.ticket_count)
})

test('overview dashboard renders relative staleness and no time series', async ({
  page,
}) => {
  await page.goto('/')
  await expect(page.getByTestId('overview-dashboard')).toBeVisible()

  await expect(page.getByTestId('overview-linear-sync-staleness')).toContainText(
    'threshold 30m'
  )
  await expect(page.getByTestId('overview-evidence-pull-staleness')).toContainText(
    'threshold 30m'
  )
  await expect(page.locator('[data-chart-kind="time-series"]')).toHaveCount(0)
  await expect(page.locator('[data-testid*="time-series"]')).toHaveCount(0)
  await expect(page.locator('canvas')).toHaveCount(0)
  await expect(page.getByText(/trend/i)).toHaveCount(0)
  await expect(page.getByText(/time series/i)).toHaveCount(0)
})
