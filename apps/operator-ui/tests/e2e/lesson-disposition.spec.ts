import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from '@playwright/test'
import {
  E2E_OPERATOR_TOKEN,
  startAtlasApiServer,
} from './atlas-api-server'

const apiBaseURL =
  process.env.ATLAS_OPERATOR_E2E_API_URL ?? 'http://127.0.0.1:18000'

const lessonTitles = {
  duplicate: 'Prevent duplicate browser commands',
  promote: 'Promote through the governed browser gate',
  refresh: 'Lose write authority on browser refresh',
  reject: 'Reject through the governed browser gate',
  stale: 'Expose a stale CLI race honestly',
} as const

const lessonIds = {
  duplicate: '00000000-0000-4000-8000-00000000a404',
  promote: '00000000-0000-4000-8000-00000000a401',
  refresh: '00000000-0000-4000-8000-00000000a405',
  reject: '00000000-0000-4000-8000-00000000a402',
  stale: '00000000-0000-4000-8000-00000000a403',
} as const

let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined

test.beforeAll(async () => {
  apiServer = await startAtlasApiServer()
})

test.afterAll(async () => {
  await apiServer?.stop()
  apiServer = undefined
})

async function signIn(page: Page): Promise<{ csrfToken: string }> {
  await page.getByRole('button', { name: 'Sign in' }).click()
  const loginDialog = page.getByRole('dialog', {
    name: /Operator sign in|Restore operator session/,
  })
  await expect(loginDialog).toBeVisible()
  await loginDialog.getByLabel('Bootstrap token').fill(E2E_OPERATOR_TOKEN)
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/v1/session') &&
      response.request().method() === 'POST'
  )
  await loginDialog.getByRole('button', { name: 'Sign in' }).click()
  const response = await responsePromise
  expect(response.status()).toBe(200)
  const payload = (await response.json()) as { csrf_token: string }
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  return { csrfToken: payload.csrf_token }
}

async function openLesson(page: Page, title: string) {
  const row = page.getByRole('row').filter({ hasText: title })
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: `View lesson details: ${title}` }).click()
  const drawer = page.getByRole('dialog', { name: title })
  await expect(drawer).toBeVisible()
  return drawer
}

async function cliReject(
  request: APIRequestContext,
  lessonId: string
): Promise<void> {
  const login = await request.post(`${apiBaseURL}/api/v1/session`, {
    data: { token: E2E_OPERATOR_TOKEN },
    headers: {
      'Content-Type': 'application/json',
      Origin: apiBaseURL,
    },
  })
  expect(login.status()).toBe(200)
  const loginPayload = (await login.json()) as { csrf_token: string }
  const ruling = await request.post(
    `${apiBaseURL}/api/v1/lessons/${lessonId}/reject`,
    {
      data: {},
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': crypto.randomUUID(),
        Origin: apiBaseURL,
        'X-Atlas-CSRF': loginPayload.csrf_token,
      },
    }
  )
  expect(ruling.status()).toBe(200)
}

test('promote uses the live governed API and consumes the returned lesson and receipt', async ({
  page,
}) => {
  await page.goto('/lessons')
  await signIn(page)
  const drawer = await openLesson(page, lessonTitles.promote)

  await drawer.getByRole('button', { name: 'Promote' }).click()
  const confirmation = page.getByRole('alertdialog', {
    name: 'Confirm lesson promotion',
  })
  await expect(confirmation).toContainText(
    'ACTIVE lessons may enter future context packs'
  )
  await confirmation.getByLabel('Operator confidence (0.0–1.0)').fill('0.812')
  await confirmation.getByRole('button', { name: 'Confirm promotion' }).click()

  await expect(drawer.getByRole('status')).toContainText('lesson.promote')
  await expect(drawer.getByRole('region', { name: 'Disposition receipt' })).toContainText(
    'action_succeeded'
  )
  await expect(drawer.getByText('Active', { exact: true }).first()).toBeVisible()
  await expect(
    page.getByRole('row').filter({ hasText: lessonTitles.promote })
  ).toHaveCount(0)
})

test('reject requires destructive archival confirmation and removes the DRAFT row', async ({
  page,
}) => {
  await page.goto('/lessons')
  await signIn(page)
  const drawer = await openLesson(page, lessonTitles.reject)

  await drawer.getByRole('button', { name: 'Reject' }).click()
  const confirmation = page.getByRole('alertdialog', {
    name: 'Confirm lesson rejection',
  })
  await expect(confirmation).toContainText('archives it for audit')
  await confirmation.getByRole('button', { name: 'Confirm rejection' }).click()

  await expect(drawer.getByRole('status')).toContainText('lesson.reject')
  await expect(drawer.getByText('Archived', { exact: true }).first()).toBeVisible()
  await expect(
    page.getByRole('row').filter({ hasText: lessonTitles.reject })
  ).toHaveCount(0)
})

test('stale CLI race displays the safe current lesson and blocks overwrite', async ({
  page,
  request,
}) => {
  await page.goto('/lessons')
  await signIn(page)
  const drawer = await openLesson(page, lessonTitles.stale)

  await cliReject(request, lessonIds.stale)
  await drawer.getByRole('button', { name: 'Promote' }).click()
  const confirmation = page.getByRole('alertdialog', {
    name: 'Confirm lesson promotion',
  })
  await confirmation.getByLabel('Operator confidence (0.0–1.0)').fill('0.7')
  await confirmation.getByRole('button', { name: 'Confirm promotion' }).click()

  await expect(drawer.getByText('Lesson changed; ruling blocked')).toBeVisible()
  await expect(drawer.getByText(/Safe current state: archived/)).toBeVisible()
  await expect(
    drawer.getByRole('button', { name: 'Close and re-review lesson' })
  ).toBeVisible()
  await expect(drawer.getByRole('button', { name: 'Promote' })).toHaveCount(0)
  await expect(drawer.getByRole('button', { name: 'Reject' })).toHaveCount(0)
})

test('in-flight guard prevents duplicate confirmation clicks from issuing another command', async ({
  page,
}) => {
  let commandCount = 0
  await page.route(
    `**/api/v1/lessons/${lessonIds.duplicate}/promote`,
    async (route) => {
      commandCount += 1
      await new Promise((resolve) => setTimeout(resolve, 350))
      await route.continue()
    }
  )
  await page.goto('/lessons')
  await signIn(page)
  const drawer = await openLesson(page, lessonTitles.duplicate)
  await drawer.getByRole('button', { name: 'Promote' }).click()
  const confirmation = page.getByRole('alertdialog', {
    name: 'Confirm lesson promotion',
  })
  await confirmation.getByLabel('Operator confidence (0.0–1.0)').fill('0.6')
  const submit = confirmation.getByRole('button', { name: 'Confirm promotion' })
  await submit.click()
  await expect(confirmation.getByRole('button', { name: 'Promoting…' })).toBeDisabled()
  await confirmation
    .getByRole('button', { name: 'Promoting…' })
    .evaluate((element: HTMLButtonElement) => element.click())

  await expect(drawer.getByRole('status')).toContainText('lesson.promote')
  expect(commandCount).toBe(1)
})

test('refresh loses in-memory write authority and persists neither operator token', async ({
  page,
  request,
}) => {
  await page.goto('/lessons')
  const { csrfToken } = await signIn(page)

  const storedBeforeRefresh = await page.evaluate(() => ({
    local: JSON.stringify(window.localStorage),
    session: JSON.stringify(window.sessionStorage),
    url: window.location.href,
  }))
  expect(JSON.stringify(storedBeforeRefresh)).not.toContain(E2E_OPERATOR_TOKEN)
  expect(JSON.stringify(storedBeforeRefresh)).not.toContain(csrfToken)

  const scriptUrls = await page.evaluate(() =>
    performance
      .getEntriesByType('resource')
      .map((entry) => entry.name)
      .filter((url) => /\.(?:js|tsx?)(?:\?|$)/.test(url))
  )
  for (const scriptUrl of scriptUrls) {
    const source = await request.get(scriptUrl)
    expect(await source.text()).not.toContain(E2E_OPERATOR_TOKEN)
  }

  await page.reload()
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
  const drawer = await openLesson(page, lessonTitles.refresh)
  await drawer.getByRole('button', { name: 'Promote' }).click()

  const restoreDialog = page.getByRole('dialog', {
    name: 'Restore operator session',
  })
  await expect(restoreDialog).toBeVisible()
  await expect(restoreDialog.getByLabel('Bootstrap token')).toHaveValue('')
  const storedAfterRefresh = await page.evaluate(() => ({
    local: JSON.stringify(window.localStorage),
    session: JSON.stringify(window.sessionStorage),
    url: window.location.href,
  }))
  expect(JSON.stringify(storedAfterRefresh)).not.toContain(E2E_OPERATOR_TOKEN)
  expect(JSON.stringify(storedAfterRefresh)).not.toContain(csrfToken)
})
