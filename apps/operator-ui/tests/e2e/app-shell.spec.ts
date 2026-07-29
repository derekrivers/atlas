import { expect, test, type Page } from '@playwright/test'
import {
  OPERATOR_TICKET_DETAIL_PLACEHOLDER_KEY,
  operatorSurfaces,
  ticketDetailHref,
} from '../../src/app-shell/surfaces'
import { startAtlasApiServer } from './atlas-api-server'

const apiBaseURL =
  process.env.ATLAS_OPERATOR_E2E_API_URL ?? 'http://127.0.0.1:18000'
const forbiddenControlText =
  /\b(write|writes|approve|approval|promote|promotion|retry)\b/i

let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined

test.beforeAll(async () => {
  apiServer = await startAtlasApiServer()
})

test.afterAll(async () => {
  await apiServer?.stop()
  apiServer = undefined
})

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

async function expectStatusFooter(page: Page) {
  const footer = page.getByRole('contentinfo', { name: 'Status staleness' })
  await expect(footer).toBeVisible()
  await expect(footer).toContainText('Linear sync:')
  await expect(footer).toContainText('Evidence pull:')
}

async function openCommandPalette(page: Page) {
  await page.getByRole('button', { name: /Search routes/ }).click()
  await expect(page.getByRole('dialog', { name: 'Command Palette' })).toBeVisible()
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

test('shell navigation, theme toggle, command palette, and footer reach every ratified surface from cold load', async ({
  page,
}) => {
  await page.goto('/')

  const sidebarLinks = page.locator('[data-sidebar="content"] a')
  await expect(sidebarLinks).toHaveCount(operatorSurfaces.length)
  await expect(
    page.locator('[data-sidebar="content"] a[href="/status"]')
  ).toHaveCount(0)

  for (const surface of operatorSurfaces) {
    await page.goto('/')
    await expect(
      page.getByRole('button', { name: 'Toggle theme' })
    ).toBeVisible()
    await expect(page.getByRole('button', { name: /Search routes/ })).toBeVisible()

    await page.getByRole('link', { exact: true, name: surface.title }).click()
    await expect(page).toHaveURL(new RegExp(`${escapeRegExp(surface.href)}$`))
    await expect(
      page.getByRole('heading', { exact: true, name: surface.placeholder.title })
    ).toBeVisible()
    await expectStatusFooter(page)
  }

  await page.goto('/')
  await openCommandPalette(page)
  for (const surface of operatorSurfaces) {
    await expect(
      page.locator('[data-slot="command-item"]').filter({
        hasText: surface.title,
      })
    ).toBeVisible()
  }

  await page
    .locator('[data-slot="command-input"]')
    .fill(OPERATOR_TICKET_DETAIL_PLACEHOLDER_KEY)
  await page
    .locator('[data-slot="command-item"]')
    .filter({ hasText: `Open ${OPERATOR_TICKET_DETAIL_PLACEHOLDER_KEY}` })
    .click()
  await expect(page).toHaveURL(
    new RegExp(
      `${escapeRegExp(ticketDetailHref(OPERATOR_TICKET_DETAIL_PLACEHOLDER_KEY))}$`
    )
  )
})

test('shell controls do not expose forbidden action vocabulary', async ({
  page,
}) => {
  await page.goto('/')

  const observedLabels: string[] = []
  observedLabels.push(...(await visibleInteractiveLabels(page)))

  await openCommandPalette(page)
  observedLabels.push(...(await visibleInteractiveLabels(page)))
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: 'Command Palette' })).toBeHidden()

  await page.getByRole('button', { name: 'Toggle theme' }).click()
  observedLabels.push(...(await visibleInteractiveLabels(page)))

  const offenders = observedLabels.filter((label) =>
    forbiddenControlText.test(label)
  )
  expect(offenders).toEqual([])
})

test('unknown operator routes render the 404 page inside the shell', async ({
  page,
}) => {
  await page.goto('/not-a-route')

  await expect(page.getByRole('heading', { name: '404' })).toBeVisible()
  await expect(page.getByText('Page Not Found')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Overview' })).toBeVisible()
  await expectStatusFooter(page)
})

test('unknown ticket keys render the API native detail body verbatim', async ({
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
  await expectStatusFooter(page)
})
