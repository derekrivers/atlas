import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { E2E_OPERATOR_TOKEN, startAtlasApiServer } from './atlas-api-server'

const axeTags = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']
const appPort = Number(process.env.ATLAS_OPERATOR_UI_E2E_PORT ?? 4173)
const appBaseURL = `http://127.0.0.1:${appPort}`
const viewports = [
  { height: 768, name: 'laptop', width: 1366 },
  { height: 768, name: 'tablet', width: 1024 },
] as const
const lessonIds = {
  conflict: '00000000-0000-4000-8000-00000000a403',
  forbidden: '00000000-0000-4000-8000-00000000a402',
  receiptFailure: '00000000-0000-4000-8000-00000000a408',
  session: '00000000-0000-4000-8000-00000000a405',
  success: '00000000-0000-4000-8000-00000000a401',
} as const
const lessonTitles = {
  conflict: 'Expose a stale CLI race honestly',
  forbidden: 'Reject through the governed browser gate',
  receiptFailure: 'Roll back a missing audit receipt',
  session: 'Lose write authority on browser refresh',
  success: 'Promote through the governed browser gate',
} as const

let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined

test.beforeAll(async () => {
  apiServer = await startAtlasApiServer({
    clock: '2099-08-11T12:00:00+00:00',
  })
})

test.afterAll(async () => {
  await apiServer?.stop()
  apiServer = undefined
})

function server() {
  if (!apiServer) {
    throw new Error('live Atlas API server was not started')
  }
  return apiServer
}

async function axeViolations(page: Page) {
  return new AxeBuilder({ page }).withTags(axeTags).analyze()
}

async function expectNoHorizontalScrolling(page: Page): Promise<void> {
  const offenders = await page.evaluate(() =>
    [
      document.documentElement,
      document.body,
      ...Array.from(document.querySelectorAll('*')),
    ]
      .filter((element) => {
        const style = window.getComputedStyle(element)
        const box = element.getBoundingClientRect()
        if (
          style.display === 'none' ||
          style.visibility === 'hidden' ||
          box.width === 0 ||
          box.height === 0
        ) {
          return false
        }
        return (
          (element === document.documentElement ||
            ['auto', 'scroll'].includes(style.overflowX)) &&
          element.scrollWidth > element.clientWidth + 1
        )
      })
      .map((element) => ({
        clientWidth: element.clientWidth,
        element: element.getAttribute('aria-label') ?? element.tagName,
        scrollWidth: element.scrollWidth,
      }))
  )
  expect(offenders).toEqual([])
}

async function expectWritableStateAccessible(
  page: Page,
  stateName: string
): Promise<void> {
  const mode = await page
    .locator('html')
    .evaluate((element) => (element.classList.contains('dark') ? 'dark' : 'light'))
  for (const viewport of viewports) {
    await page.setViewportSize(viewport)
    const result = await axeViolations(page)
    expect(
      result.violations,
      `${stateName} ${mode} ${viewport.name}: ${result.violations
        .map((violation) => violation.id)
        .join(', ')}`
    ).toEqual([])
    await expectNoHorizontalScrolling(page)
  }
}

async function setColorMode(page: Page, mode: 'dark' | 'light'): Promise<void> {
  await page.context().addCookies([
    {
      name: 'vite-ui-theme',
      url: appBaseURL,
      value: mode,
    },
  ])
}

async function expectMeaningfulFocus(page: Page, stateName: string) {
  const focus = await page.evaluate(() => {
    const active = document.activeElement
    if (!(active instanceof HTMLElement)) {
      return null
    }
    const box = active.getBoundingClientRect()
    return {
      label:
        active.getAttribute('aria-label') ??
        active.textContent?.replace(/\s+/g, ' ').trim() ??
        active.tagName,
      tagName: active.tagName,
      visible: box.width > 0 && box.height > 0,
    }
  })
  expect(focus, `${stateName} should retain meaningful focus`).not.toBeNull()
  expect(focus?.tagName, `${stateName} should not focus the document body`).not.toBe(
    'BODY'
  )
  expect(focus?.visible, `${stateName} focus should remain visible`).toBe(true)
}

async function pageSignIn(page: Page): Promise<string> {
  await page.getByRole('button', { name: 'Sign in' }).click()
  const dialog = page.getByRole('dialog', { name: 'Operator sign in' })
  await expect(dialog.getByLabel('Bootstrap token')).toBeFocused()
  await dialog.getByLabel('Bootstrap token').fill(E2E_OPERATOR_TOKEN)
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/v1/session') &&
      response.request().method() === 'POST'
  )
  await dialog.getByRole('button', { name: 'Sign in' }).click()
  const response = await responsePromise
  expect(response.status()).toBe(200)
  const body = (await response.json()) as { csrf_token: string }
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  return body.csrf_token
}

async function openLesson(page: Page, title: string) {
  const row = page.getByRole('row').filter({ hasText: title })
  await row
    .getByRole('button', { name: `View lesson details: ${title}` })
    .click()
  const drawer = page.getByRole('dialog', { name: title })
  await expect(drawer).toBeVisible()
  return drawer
}

async function openPromotion(page: Page, drawer: Locator) {
  const trigger = drawer.getByRole('button', { name: 'Promote' })
  await trigger.click()
  const confirmation = page.getByRole('alertdialog', {
    name: 'Confirm lesson promotion',
  })
  await expect(confirmation).toBeVisible()
  return { confirmation, trigger }
}

test('login, confirmation, validation, busy and success states pass keyboard, announcement, contrast and responsive gates @accessibility', async ({
  page,
}) => {
  await setColorMode(page, 'light')
  await page.goto('/lessons')
  await page.getByRole('button', { name: 'Sign in' }).click()
  const login = page.getByRole('dialog', { name: 'Operator sign in' })
  await expect(login.getByLabel('Bootstrap token')).toBeFocused()
  await login.getByLabel('Bootstrap token').fill('wrong-token-canary-atl-424')
  await login.getByRole('button', { name: 'Sign in' }).click()
  const loginError = login.getByRole('alert')
  await expect(loginError).toContainText('bootstrap token was refused')
  await expectWritableStateAccessible(page, 'login error')
  await expectMeaningfulFocus(page, 'login error')

  await login.getByLabel('Bootstrap token').fill(E2E_OPERATOR_TOKEN)
  await login.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  const drawer = await openLesson(page, lessonTitles.success)
  const { confirmation, trigger } = await openPromotion(page, drawer)
  await expectWritableStateAccessible(page, 'promotion confirmation')
  for (let step = 0; step < 5; step += 1) {
    await page.keyboard.press('Tab')
    expect(
      await page.evaluate(() =>
        Boolean(document.activeElement?.closest('[role="alertdialog"]'))
      )
    ).toBe(true)
  }
  await page.keyboard.press('Escape')
  await expect(confirmation).toBeHidden()
  await expect(trigger).toBeFocused()

  const reopened = await openPromotion(page, drawer)
  await reopened.confirmation
    .getByLabel('Operator confidence (0.0–1.0)')
    .fill('2')
  await reopened.confirmation
    .getByRole('button', { name: 'Confirm promotion' })
    .click()
  await expect(reopened.confirmation.getByRole('alert')).toContainText(
    'finite confidence'
  )
  await expectWritableStateAccessible(page, 'promotion validation error')

  let releaseRequest: (() => void) | undefined
  const requestGate = new Promise<void>((resolve) => {
    releaseRequest = resolve
  })
  const commandPattern = `**/api/v1/lessons/${lessonIds.success}/promote`
  await page.route(commandPattern, async (route) => {
    await requestGate
    await route.continue()
  })
  await reopened.confirmation
    .getByLabel('Operator confidence (0.0–1.0)')
    .fill('0.78')
  await reopened.confirmation
    .getByRole('button', { name: 'Confirm promotion' })
    .click()
  await expect(
    reopened.confirmation.getByRole('button', { name: 'Promoting…' })
  ).toBeDisabled()
  await expect(reopened.confirmation).toHaveAttribute('aria-busy', 'true')
  await expectWritableStateAccessible(page, 'promotion busy')
  releaseRequest?.()

  await expect(drawer.getByRole('status')).toContainText('action_succeeded')
  await page.unroute(commandPattern)
  await expect(drawer.getByRole('status')).toHaveAttribute('aria-live', 'polite')
  await expectWritableStateAccessible(page, 'promotion success')
  await expectMeaningfulFocus(page, 'promotion success')
})

test('security refusal has an assertive announcement, focus recovery and responsive contrast @accessibility', async ({
  page,
}) => {
  await setColorMode(page, 'dark')
  await page.goto('/lessons')
  await pageSignIn(page)
  const drawer = await openLesson(page, lessonTitles.forbidden)
  const { confirmation, trigger } = await openPromotion(page, drawer)
  await page.route(
    `**/api/v1/lessons/${lessonIds.forbidden}/promote`,
    async (route) => {
      await route.continue({
        headers: {
          ...route.request().headers(),
          'x-atlas-csrf': 'wrong-csrf-canary-atl-424',
        },
      })
    }
  )
  await confirmation
    .getByLabel('Operator confidence (0.0–1.0)')
    .fill('0.5')
  await confirmation.getByRole('button', { name: 'Confirm promotion' }).click()
  const refusal = drawer.getByRole('alert')
  await expect(refusal).toContainText('Security refusal')
  await expect(refusal).toHaveAttribute('aria-live', 'assertive')
  await expectWritableStateAccessible(page, 'security refusal')
  await expect(trigger).toBeFocused()
})

test('concurrent conflict announces the safe state and exposes keyboard recovery on both viewports @accessibility', async ({
  page,
}) => {
  await setColorMode(page, 'light')
  await page.goto('/lessons')
  await pageSignIn(page)
  const drawer = await openLesson(page, lessonTitles.conflict)
  const cli = server().runCli(['lessons', 'reject', lessonIds.conflict])
  expect(cli.status, cli.stderr).toBe(0)
  const { confirmation } = await openPromotion(page, drawer)
  await confirmation
    .getByLabel('Operator confidence (0.0–1.0)')
    .fill('0.5')
  await confirmation.getByRole('button', { name: 'Confirm promotion' }).click()
  const conflict = drawer.getByRole('alert')
  await expect(conflict).toContainText('Lesson changed; ruling blocked')
  await expect(conflict).toContainText('Safe current state: archived')
  await expect(conflict).toHaveAttribute('aria-live', 'assertive')
  const recovery = conflict.getByRole('button', {
    name: 'Close and re-review lesson',
  })
  await expect(recovery).toBeVisible()
  await expectWritableStateAccessible(page, 'concurrent conflict')
  await recovery.focus()
  await expect(recovery).toBeFocused()
})

test('revoked-session 401 closes stale ruling state and focuses the announced restore flow @accessibility', async ({
  page,
}) => {
  await setColorMode(page, 'dark')
  await page.goto('/lessons')
  const csrfToken = await pageSignIn(page)
  const drawer = await openLesson(page, lessonTitles.session)
  const { confirmation } = await openPromotion(page, drawer)
  await confirmation
    .getByLabel('Operator confidence (0.0–1.0)')
    .fill('0.5')
  const logoutStatus = await page.evaluate(async (csrf) => {
    const response = await fetch('/api/v1/session', {
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-Atlas-CSRF': csrf,
      },
      method: 'DELETE',
    })
    return response.status
  }, csrfToken)
  expect(logoutStatus).toBe(200)
  await confirmation.getByRole('button', { name: 'Confirm promotion' }).click()
  const expired = page.getByRole('dialog', { name: 'Session expired' })
  await expect(expired).toBeVisible()
  await expect(expired.getByLabel('Bootstrap token')).toBeFocused()
  await expect(page.getByRole('dialog', { name: lessonTitles.session })).toHaveCount(
    0
  )
  await expectWritableStateAccessible(page, 'revoked session')
})

test('atomic receipt failure exposes an accessible ambiguous-error recovery state @accessibility', async ({
  page,
}) => {
  await server().restart({ receiptFailure: true })
  await setColorMode(page, 'dark')
  await page.goto('/lessons')
  await pageSignIn(page)
  const drawer = await openLesson(page, lessonTitles.receiptFailure)
  const { confirmation } = await openPromotion(page, drawer)
  await confirmation
    .getByLabel('Operator confidence (0.0–1.0)')
    .fill('0.5')
  await confirmation.getByRole('button', { name: 'Confirm promotion' }).click()
  const failure = drawer.getByRole('alert')
  await expect(failure).toContainText('did not return an unambiguous result')
  await expect(failure).toHaveAttribute('aria-live', 'assertive')
  await expect(failure.getByRole('button', { name: 'Retry safely' })).toBeVisible()
  await expectWritableStateAccessible(page, 'receipt failure')
  await failure.getByRole('button', { name: 'Retry safely' }).focus()
  await expect(failure.getByRole('button', { name: 'Retry safely' })).toBeFocused()
  await server().restart({ receiptFailure: false })
})

test('API-unreachable error remains accessible and responsive without retaining a secret artifact @accessibility', async ({
  page,
}, testInfo) => {
  await setColorMode(page, 'light')
  await page.route('**/api/v1/**', async (route) => route.abort('failed'))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'API unreachable' })).toBeVisible()
  await expectWritableStateAccessible(page, 'API unreachable')
  expect(testInfo.attachments).toEqual([])
})
