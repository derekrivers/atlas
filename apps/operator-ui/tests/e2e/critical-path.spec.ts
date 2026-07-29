import { expect, test, type APIRequestContext } from '@playwright/test'
import { ticketDetailHref } from '../../src/app-shell/surfaces'
import type { components } from '../../src/api/atlas-openapi'
import { liveApiShapeAssertions } from './live-api-shape'
import { startAtlasApiServer } from './atlas-api-server'

type CriticalPathResponse =
  components['schemas']['DependencyCriticalPathResponse']

const apiBaseURL =
  process.env.ATLAS_OPERATOR_E2E_API_URL ?? 'http://127.0.0.1:18000'

let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined
const assertCriticalPathResponse: (
  value: unknown
) => asserts value is CriticalPathResponse =
  liveApiShapeAssertions['/api/v1/dependencies/critical-path']

test.beforeAll(async () => {
  apiServer = await startAtlasApiServer()
})

test.afterAll(async () => {
  await apiServer?.stop()
  apiServer = undefined
})

async function getCriticalPath(
  request: APIRequestContext
): Promise<CriticalPathResponse> {
  const response = await request.get(
    `${apiBaseURL}/api/v1/dependencies/critical-path`
  )
  expect(response.ok(), 'critical path route should return 2xx').toBe(true)
  const payload: unknown = await response.json()
  assertCriticalPathResponse(payload)
  return payload
}

test('critical path view renders the seeded live API chain exactly', async ({
  page,
  request,
}) => {
  const criticalPath = await getCriticalPath(request)

  expect(criticalPath.keys).toEqual(['ATLAS-2'])
  expect(criticalPath.steps).toEqual([
    { key: 'ATLAS-2', effort: 3, cumulative_effort: 3 },
  ])
  expect(criticalPath.total_effort).toBe(3)

  await page.goto('/critical-path')

  await expect(
    page.getByRole('heading', { exact: true, name: 'Critical Path' })
  ).toBeVisible()
  const advisory = page.getByLabel('Critical path advisory')
  await expect(advisory).toContainText('ADVISORY')
  await expect(advisory).toContainText(
    'The critical path does not gate dispatch.'
  )
  await expect(page.getByTestId('critical-path-total')).toHaveText(
    String(criticalPath.total_effort)
  )

  const rows = page.getByTestId('critical-path-step')
  await expect(rows).toHaveCount(criticalPath.steps.length)

  for (const [index, step] of criticalPath.steps.entries()) {
    const row = rows.nth(index)
    const link = row.getByRole('link', { name: step.key })

    await expect(link).toHaveAttribute('href', ticketDetailHref(step.key))
    await expect(row.getByTestId('critical-path-step-effort')).toHaveText(
      String(step.effort)
    )
    await expect(row.getByTestId('critical-path-step-cumulative')).toHaveText(
      String(step.cumulative_effort)
    )
  }
})
