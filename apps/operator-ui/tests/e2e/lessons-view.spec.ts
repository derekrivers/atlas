import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { assertLessonsResponse } from './live-api-shape'
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

function formatEnumValue(value: string): string {
  return value
    .split('_')
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(' ')
}

async function getJson(request: APIRequestContext, path: string): Promise<unknown> {
  const response = await request.get(`${apiBaseURL}${path}`)
  expect(response.ok(), `${path} should return 2xx`).toBe(true)
  return response.json()
}

async function visibleInteractiveLabels(page: Page): Promise<string[]> {
  return page
    .locator(
      [
        'a',
        'button',
        '[role="button"]',
        '[role="link"]',
        '[role="menuitem"]',
        '[role="tab"]',
        '[data-slot="command-item"]',
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

test('lessons view renders the seeded draft queue and full detail drawer', async ({
  page,
  request,
}) => {
  const payload = await getJson(request, '/api/v1/lessons')
  assertLessonsResponse(payload)
  const lesson = payload.lessons[0]

  await page.goto('/lessons')

  await expect(
    page.getByRole('heading', { exact: true, name: 'Lessons' })
  ).toBeVisible()
  await expect(page.getByRole('tab', { name: /Draft/ })).toHaveAttribute(
    'aria-selected',
    'true'
  )
  await expect(page.getByRole('columnheader', { name: 'Category' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Title' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Status' })).toBeVisible()
  await expect(
    page.getByRole('columnheader', { name: 'Confidence' })
  ).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Tags' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Creator' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Created' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Updated' })).toBeVisible()

  const row = page.getByRole('row').filter({ hasText: lesson.title })
  await expect(row.getByText(formatEnumValue(lesson.category))).toBeVisible()
  await expect(row.getByText(lesson.title)).toBeVisible()
  await expect(row.getByText(formatEnumValue(lesson.status))).toBeVisible()
  await expect(
    row.getByText(lesson.confidence?.toFixed(2) ?? 'Unscored')
  ).toBeVisible()
  await expect(row.getByText(lesson.tags[0]).first()).toBeVisible()
  await expect(
    row.getByText(`${lesson.created_by_type} / ${lesson.created_by_id}`)
  ).toBeVisible()
  await expect(row.getByText(lesson.created_at).first()).toBeVisible()
  await expect(row.getByText(lesson.updated_at).first()).toBeVisible()
  await expect(row.getByText(lesson.source_ticket_id)).toBeVisible()

  await page
    .getByRole('button', { name: `View lesson details: ${lesson.title}` })
    .click()

  const drawer = page.getByRole('dialog', { name: lesson.title })
  await expect(drawer).toBeVisible()
  await expect(drawer.getByText(lesson.problem, { exact: true })).toBeVisible()
  await expect(drawer.getByText(lesson.solution, { exact: true })).toBeVisible()
  await expect(drawer.getByText(lesson.outcome, { exact: true })).toBeVisible()
  await expect(drawer.getByText(lesson.source_ticket_id, { exact: true })).toBeVisible()
  await expect(
    drawer.getByText(lesson.related_ticket_ids[0], { exact: true })
  ).toBeVisible()
})

test('lesson ticket UUIDs are literal text, not interactive links or controls', async ({
  page,
  request,
}) => {
  const payload = await getJson(request, '/api/v1/lessons')
  assertLessonsResponse(payload)
  const lesson = payload.lessons[0]
  const ticketIds = [lesson.source_ticket_id, ...lesson.related_ticket_ids]

  await page.goto('/lessons')
  await page
    .getByRole('button', { name: `View lesson details: ${lesson.title}` })
    .click()

  for (const ticketId of ticketIds) {
    await expect(page.getByText(ticketId, { exact: true }).first()).toBeVisible()
    await expect(page.getByRole('link', { name: ticketId })).toHaveCount(0)
  }

  const interactiveLabels = await visibleInteractiveLabels(page)
  expect(
    interactiveLabels.filter((label) =>
      ticketIds.some((ticketId) => label.includes(ticketId))
    )
  ).toEqual([])
})

test('lesson facets and drawer do not expose write controls', async ({ page }) => {
  await page.goto('/lessons')
  const observedLabels: string[] = []

  for (const label of ['Draft', 'Active', 'Archived', 'Deprecated']) {
    await page.getByRole('tab', { name: new RegExp(label) }).click()
    observedLabels.push(...(await visibleInteractiveLabels(page)))
  }

  await page.getByRole('tab', { name: /Draft/ }).click()
  await page.getByRole('button', { name: /View lesson details:/ }).click()
  observedLabels.push(...(await visibleInteractiveLabels(page)))

  const offenders = observedLabels.filter((label) =>
    /\b(promote|reject|archive|merge)\b/i.test(label)
  )
  expect(offenders).toEqual([])
})
