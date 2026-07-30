import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { atlasOpenApiEnums } from '../../src/api/atlas-openapi-runtime'
import { assertReviewQueueResponse } from './live-api-shape'
import { startAtlasApiServer } from './atlas-api-server'

const apiBaseURL =
  process.env.ATLAS_OPERATOR_E2E_API_URL ?? 'http://127.0.0.1:18000'
const reviewQueueSeedPath = join(
  dirname(fileURLToPath(import.meta.url)),
  'fixtures',
  'review-queue-seed.json'
)
const forbiddenReviewControlText =
  /\b(approve|reject|request changes|request-changes|retry)\b/i

test.describe.configure({ mode: 'serial' })

async function getJson(request: APIRequestContext, path: string): Promise<unknown> {
  const response = await request.get(`${apiBaseURL}${path}`)
  expect(response.ok(), `${path} should return 2xx`).toBe(true)
  return response.json()
}

function labelFromValue(value: string): string {
  return value
    .split('_')
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(' ')
}

async function visibleInteractiveLabels(page: Page): Promise<string[]> {
  return page
    .locator(
      [
        'a',
        'button',
        'input',
        'select',
        'textarea',
        '[role="button"]',
        '[role="link"]',
      ].join(',')
    )
    .evaluateAll((elements) =>
      elements
        .filter((element) => {
          const style = window.getComputedStyle(element)
          const box = element.getBoundingClientRect()
          return (
            style.visibility !== 'hidden' &&
            style.display !== 'none' &&
            box.width > 0 &&
            box.height > 0
          )
        })
        .map((element) =>
          [
            element.getAttribute('aria-label'),
            element.textContent,
            element.getAttribute('title'),
          ]
            .filter(Boolean)
            .join(' ')
            .replace(/\s+/g, ' ')
            .trim()
        )
        .filter(Boolean)
    )
}

test.describe('review queue view with an empty live queue', () => {
  let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined

  test.beforeAll(async () => {
    apiServer = await startAtlasApiServer()
  })

  test.afterAll(async () => {
    await apiServer?.stop()
    apiServer = undefined
  })

  test('renders the shared empty state for the seeded empty queue', async ({
    page,
    request,
  }) => {
    const payload = await getJson(request, '/api/v1/reviews')
    assertReviewQueueResponse(payload)
    expect(payload.reviews).toEqual([])

    await page.goto('/reviews')

    await expect(
      page.getByRole('heading', { name: 'Acceptance Review' })
    ).toBeVisible()
    await expect(page.getByText('0 waiting')).toBeVisible()
    await expect(
      page.getByRole('status').filter({ hasText: 'No tickets awaiting review' })
    ).toBeVisible()
  })
})

test.describe('review queue view with a populated live queue', () => {
  let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined

  test.beforeAll(async () => {
    apiServer = await startAtlasApiServer({ seedPath: reviewQueueSeedPath })
  })

  test.afterAll(async () => {
    await apiServer?.stop()
    apiServer = undefined
  })

  test('renders verdicts, gates, checks, links, and API order', async ({
    page,
    request,
  }) => {
    const payload = await getJson(request, '/api/v1/reviews')
    assertReviewQueueResponse(payload)
    expect(payload.reviews).toHaveLength(2)

    await page.goto('/reviews')

    const items = page.getByTestId('review-queue-item')
    await expect(items).toHaveCount(payload.reviews.length)
    const renderedKeys = await items.evaluateAll((elements) =>
      elements.map((element) => element.getAttribute('data-ticket-key'))
    )
    expect(renderedKeys).toEqual(payload.reviews.map((review) => review.key))

    for (const [index, review] of payload.reviews.entries()) {
      const item = items.nth(index)
      const link = item.getByRole('link', {
        name: new RegExp(`^${review.key}\\s`),
      })

      await expect(link).toHaveAttribute('href', `/tickets/${review.key}`)
      await expect(item).toContainText(`Verdict ${labelFromValue(review.verdict)}`)

      const systemGate = item.getByTestId('review-gate-system-evidence')
      await expect(systemGate).toHaveAttribute(
        'data-gate-state',
        review.has_system_evidence ? 'pass' : 'fail'
      )
      await expect(systemGate).toContainText(
        review.has_system_evidence ? 'Pass' : 'Fail'
      )

      const prMergedGate = item.getByTestId('review-gate-pr-merged-evidence')
      await expect(prMergedGate).toHaveAttribute(
        'data-gate-state',
        review.has_pr_merged_evidence ? 'pass' : 'fail'
      )
      await expect(prMergedGate).toContainText(
        review.has_pr_merged_evidence ? 'Pass' : 'Fail'
      )

      await expect(
        item.getByRole('table', { name: 'Verification checks' })
      ).toBeVisible()
      const checkRows = item.getByTestId('review-check-row')
      await expect(checkRows).toHaveCount(
        atlasOpenApiEnums.VerificationCheckType.length
      )
      const renderedCheckTypes = await checkRows.evaluateAll((rows) =>
        rows.map((row) => row.getAttribute('data-check-type'))
      )
      expect(renderedCheckTypes).toEqual([
        ...atlasOpenApiEnums.VerificationCheckType,
      ])

      const latestCheckState = new Map(
        review.checks.map((check) => [check.check_type, check.status])
      )
      const renderedCheckStates = await checkRows.evaluateAll((rows) =>
        Object.fromEntries(
          rows.map((row) => [
            row.getAttribute('data-check-type'),
            row.getAttribute('data-check-state'),
          ])
        )
      )
      for (const checkType of atlasOpenApiEnums.VerificationCheckType) {
        expect(renderedCheckStates[checkType]).toBe(
          latestCheckState.get(checkType) ?? 'not_run'
        )
      }
    }
  })

  test('does not expose review action controls in the populated queue', async ({
    page,
  }) => {
    await page.goto('/reviews')

    const offenders = (await visibleInteractiveLabels(page)).filter((label) =>
      forbiddenReviewControlText.test(label)
    )
    expect(offenders).toEqual([])
  })
})
