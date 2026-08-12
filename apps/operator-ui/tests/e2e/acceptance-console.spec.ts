import AxeBuilder from '@axe-core/playwright'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test, type Page, type Request } from '@playwright/test'
import {
  E2E_OPERATOR_TOKEN,
  startAtlasApiServer,
} from './atlas-api-server'

const acceptanceSeedPath = join(
  dirname(fileURLToPath(import.meta.url)),
  'fixtures',
  'acceptance-console-seed.json'
)
const exactHead = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const movedHead = 'cccccccccccccccccccccccccccccccccccccccc'
const forbiddenAction =
  /\b(?:auto-merge|merge|rebase|linear status|symphony|schema upgrade|pm sync)\b/i

let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  apiServer = await startAtlasApiServer({
    acceptance: true,
    clock: '2030-08-12T12:00:00+00:00',
    seedPath: acceptanceSeedPath,
  })
})

test.afterAll(async () => {
  await apiServer?.stop()
  apiServer = undefined
})

async function signIn(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Sign in' }).click()
  const dialog = page.getByRole('dialog', {
    name: /Operator sign in|Restore operator session/,
  })
  await dialog.getByLabel('Bootstrap token').fill(E2E_OPERATOR_TOKEN)
  await dialog.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
}

async function sessionId(page: Page): Promise<string> {
  const value = await page
    .locator('dt')
    .filter({ hasText: /^Session ID$/ })
    .locator('..')
    .locator('dd')
    .textContent()
  expect(value).toMatch(/^[0-9a-f-]{36}$/)
  return value as string
}

async function createSession(page: Page): Promise<string> {
  await page.goto('/reviews/ATLAS-243/acceptance')
  await signIn(page)
  await expect(page.getByLabel('Repository')).toHaveValue('acme/atlas')
  await expect(page.getByLabel('Pull request number')).toHaveValue('415')
  await page.getByRole('button', { name: 'Create exact-head session' }).click()
  await expect(page.getByText('Preflight Passed', { exact: true }).first()).toBeVisible()
  await expect(page.locator('h1')).toBeFocused()
  return sessionId(page)
}

async function visibleInteractiveLabels(page: Page): Promise<string[]> {
  return page
    .locator('a, button, input, select, textarea, [role="button"], [role="link"]')
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

function idempotencyKey(request: Request): string | undefined {
  return request.headers()['idempotency-key']
}

test('runs the exact-head sequence and revokes manual guidance on live movement or failure', async ({
  page,
}) => {
  apiServer?.setAcceptanceState({ mode: 'current' })
  await createSession(page)

  await page.getByRole('button', { name: 'Pull evidence' }).click()
  await expect(page.getByText('Evidence Ready', { exact: true }).first()).toBeVisible()
  await expect(page.locator('h1')).toBeFocused()

  const criteria = page.getByRole('checkbox')
  await expect(criteria).toHaveCount(3)
  for (let index = 0; index < 3; index += 1) {
    await criteria.nth(index).click()
  }
  await page.getByRole('button', { name: 'Confirm every criterion' }).click()
  await expect(
    page.getByText('Confirmations Ready', { exact: true }).first()
  ).toBeVisible()

  await page.getByRole('button', { name: 'Run verification' }).click()
  const ready = page.getByText('Exact verified head is ready for manual merge')
  await expect(ready).toBeVisible()
  const readyAlert = ready.locator('..')
  await expect(readyAlert).toContainText(exactHead)
  await expect(readyAlert).toContainText('manually in GitHub')
  await expect(
    page.locator('dt').filter({ hasText: /^Top-level verdict$/ }).locator('..')
  ).toContainText('Passed')
  await expect(
    page.getByRole('region', { name: 'Canonical verification matrix for ATLAS-243' })
      .getByTestId('review-check-row')
  ).toHaveCount(7)

  const interactiveOffenders = (await visibleInteractiveLabels(page)).filter(
    (label) => forbiddenAction.test(label)
  )
  expect(interactiveOffenders).toEqual([])

  for (const viewport of [
    { height: 768, width: 1366 },
    { height: 768, width: 1024 },
  ]) {
    await page.setViewportSize(viewport)
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1
      )
    ).toBe(true)
  }
  const axe = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  expect(axe.violations).toEqual([])

  apiServer?.setAcceptanceState({ mode: 'head-moved' })
  await page.getByRole('button', { name: 'Refresh current state' }).click()
  await expect(page.getByText('Live merge readiness is closed')).toBeVisible()
  await expect(page.getByText(/Head Sha Mismatch/)).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Start a new exact-head session' })
  ).toBeVisible()
  await expect(ready).toHaveCount(0)

  apiServer?.setAcceptanceState({ mode: 'current' })
  await page.getByRole('button', { name: 'Refresh current state' }).click()
  await expect(ready).toBeVisible()

  apiServer?.setAcceptanceState({ mode: 'failure' })
  await page.getByRole('button', { name: 'Refresh current state' }).click()
  await expect(page.getByText(/External Read Failed/)).toBeVisible()
  await expect(page.getByText(/External State Indeterminate/)).toBeVisible()
  await expect(ready).toHaveCount(0)
  apiServer?.setAcceptanceState({ mode: 'current' })
})

test('requires stale-session recovery and a fresh key after an unambiguous timeout', async ({
  page,
}) => {
  await apiServer?.stop()
  apiServer = await startAtlasApiServer({
    acceptance: true,
    clock: '2030-08-12T12:00:00+00:00',
    seedPath: acceptanceSeedPath,
  })
  apiServer?.setAcceptanceState({ mode: 'current' })
  await createSession(page)

  apiServer?.setAcceptanceState({ mode: 'head-moved' })
  await page.getByRole('button', { name: 'Pull evidence' }).click()
  await expect(page.getByText('Exact-head session is stale')).toBeVisible()
  await expect(page.getByText(/new acceptance session/i)).toBeVisible()
  await page.getByRole('button', { name: 'Refresh stale session' }).click()
  await page.getByRole('button', { name: 'Start a new exact-head session' }).click()
  await expect(page.getByLabel('Repository')).toBeFocused()
  await page.getByRole('button', { name: 'Create exact-head session' }).click()
  const currentSessionId = await sessionId(page)
  await expect(page.getByText(movedHead, { exact: true }).first()).toBeVisible()

  const evidenceKeys: string[] = []
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      /\/acceptance-sessions\/[0-9a-f-]+\/evidence$/.test(request.url())
    ) {
      const key = idempotencyKey(request)
      if (key) evidenceKeys.push(key)
    }
  })

  apiServer?.setAcceptanceState({ mode: 'timeout' })
  await page.getByRole('button', { name: 'Pull evidence' }).click()
  await expect(page.getByText('Acceptance action timed out')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Retry same command key' })).toHaveCount(0)

  apiServer?.setAcceptanceState({ mode: 'head-moved' })
  await page.getByRole('button', { name: 'Refresh before new command' }).click()
  await page.getByRole('button', { name: 'Pull evidence' }).click()
  await expect(page.getByText('Evidence Ready', { exact: true }).first()).toBeVisible()
  expect(evidenceKeys).toHaveLength(2)
  expect(evidenceKeys[1]).not.toBe(evidenceKeys[0])

  const keyboardCriteria = page.getByRole('checkbox')
  await expect(keyboardCriteria).toHaveCount(3)
  await keyboardCriteria.first().focus()
  await expect(keyboardCriteria.first()).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(keyboardCriteria.nth(1)).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(keyboardCriteria.nth(2)).toBeFocused()

  const observer = await page.context().newPage()
  await observer.goto('/reviews/ATLAS-243/acceptance')
  await signIn(observer)
  await observer.getByLabel('Session ID').fill(currentSessionId)
  await observer.getByRole('button', { name: 'Load session with fresh GET' }).click()
  await expect(
    observer.getByText('Evidence Ready', { exact: true }).first()
  ).toBeVisible()
  await expect(observer.getByText(movedHead, { exact: true }).first()).toBeVisible()

  await apiServer?.restart()
  await observer.getByRole('button', { name: 'Refresh current state' }).click()
  await expect(observer.getByRole('dialog', { name: 'Session expired' })).toBeVisible()
  await expect(observer.getByLabel('Bootstrap token')).toHaveValue('')
})
