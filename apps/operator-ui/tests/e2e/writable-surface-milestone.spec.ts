import {
  expect,
  test,
  type APIRequestContext,
  type APIResponse,
  type Page,
} from '@playwright/test'
import {
  E2E_OPERATOR_TOKEN,
  startAtlasApiServer,
  type AtlasStoreProbe,
} from './atlas-api-server'

const appPort = Number(process.env.ATLAS_OPERATOR_UI_E2E_PORT ?? 4173)
const appBaseURL = `http://127.0.0.1:${appPort}`
const apiBaseURL =
  process.env.ATLAS_OPERATOR_E2E_API_URL ?? 'http://127.0.0.1:18000'

const actorInjectionCanary = 'agent/canary-actor-injection-atl-424'
const receiptFailureCanary = 'canary-receipt-failure-atl-424'
const lessonIds = {
  cliRace: '00000000-0000-4000-8000-00000000a403',
  hostile: '00000000-0000-4000-8000-00000000a405',
  promote: '00000000-0000-4000-8000-00000000a401',
  receiptFailure: '00000000-0000-4000-8000-00000000a408',
  reject: '00000000-0000-4000-8000-00000000a402',
  replay: '00000000-0000-4000-8000-00000000a404',
  replayUx: '00000000-0000-4000-8000-00000000a409',
  twoTabs: '00000000-0000-4000-8000-00000000a407',
} as const
const lessonTitles = {
  cliRace: 'Expose a stale CLI race honestly',
  promote: 'Promote through the governed browser gate',
  receiptFailure: 'Roll back a missing audit receipt',
  reject: 'Reject through the governed browser gate',
  replayUx: 'Replay an ambiguous browser result safely',
  twoTabs: 'Resolve two browser tabs atomically',
} as const

let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined
const browserOutput: string[] = []
const responseErrors: string[] = []
const secretCanaries = new Set<string>([
  E2E_OPERATOR_TOKEN,
  actorInjectionCanary,
  receiptFailureCanary,
])

test.beforeAll(async () => {
  apiServer = await startAtlasApiServer({
    clock: '2099-08-11T12:00:00+00:00',
  })
})

test.afterAll(async () => {
  if (apiServer) {
    for (const secret of secretCanaries) {
      expect(apiServer.output()).not.toContain(secret)
    }
    await apiServer.stop()
  }
  apiServer = undefined
})

function server() {
  if (!apiServer) {
    throw new Error('live Atlas API server was not started')
  }
  return apiServer
}

function watchBrowserOutput(page: Page): void {
  page.on('console', (message) => browserOutput.push(message.text()))
  page.on('pageerror', (error) => browserOutput.push(error.message))
  page.on('request', (request) => browserOutput.push(request.url()))
}

async function pageSignIn(page: Page): Promise<string> {
  await page.getByRole('button', { name: 'Sign in' }).click()
  const dialog = page.getByRole('dialog', {
    name: /Operator sign in|Restore operator session/,
  })
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
  secretCanaries.add(body.csrf_token)
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  return body.csrf_token
}

async function apiSignIn(request: APIRequestContext): Promise<string> {
  const response = await request.post(`${apiBaseURL}/api/v1/session`, {
    data: { token: E2E_OPERATOR_TOKEN },
    headers: {
      'Content-Type': 'application/json',
      Host: new URL(apiBaseURL).host,
    },
  })
  expect(response.status()).toBe(200)
  const body = (await response.json()) as { csrf_token: string }
  secretCanaries.add(body.csrf_token)
  return body.csrf_token
}

async function openLesson(page: Page, title: string) {
  const row = page.getByRole('row').filter({ hasText: title })
  await expect(row).toBeVisible()
  await row
    .getByRole('button', { name: `View lesson details: ${title}` })
    .click()
  const drawer = page.getByRole('dialog', { name: title })
  await expect(drawer).toBeVisible()
  return drawer
}

function mutationHeaders(
  csrfToken: string,
  idempotencyKey: string
): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    'Idempotency-Key': idempotencyKey,
    Origin: apiBaseURL,
    'X-Atlas-CSRF': csrfToken,
  }
}

function targetSuccessReceipts(probe: AtlasStoreProbe, lessonId: string) {
  return probe.receipts.filter(
    (receipt) =>
      receipt.target.id === lessonId &&
      receipt.outcome === 'succeeded' &&
      receipt.result_code === 'action_succeeded'
  )
}

function expectSecretFree(value: string): void {
  for (const secret of secretCanaries) {
    expect(value).not.toContain(secret)
  }
}

test('operator-run milestone promotes and rejects through the built UI and proves repository outcomes', async ({
  page,
}) => {
  watchBrowserOutput(page)
  await page.goto('/lessons')
  await pageSignIn(page)

  const promoteDrawer = await openLesson(page, lessonTitles.promote)
  await expect(promoteDrawer).toContainText(
    'A browser promotion needs authenticated operator confidence.'
  )
  await expect(promoteDrawer).toContainText(
    'Confirm the ruling and submit the generated command contract.'
  )
  await expect(promoteDrawer).toContainText(
    'The live API returns the authoritative ACTIVE lesson and receipt.'
  )
  await promoteDrawer.getByRole('button', { name: 'Promote' }).click()
  const promotion = page.getByRole('alertdialog', {
    name: 'Confirm lesson promotion',
  })
  await promotion
    .getByLabel('Operator confidence (0.0–1.0)')
    .fill('0.812')
  await promotion.getByRole('button', { name: 'Confirm promotion' }).click()
  await expect(promoteDrawer.getByRole('status')).toContainText(
    'lesson.promote'
  )
  await expect(
    promoteDrawer.getByRole('region', { name: 'Disposition receipt' })
  ).toContainText('action_succeeded')
  await promoteDrawer.getByRole('button', { name: 'Close' }).click()

  const rejectDrawer = await openLesson(page, lessonTitles.reject)
  await rejectDrawer.getByRole('button', { name: 'Reject' }).click()
  const rejection = page.getByRole('alertdialog', {
    name: 'Confirm lesson rejection',
  })
  await rejection.getByRole('button', { name: 'Confirm rejection' }).click()
  await expect(rejectDrawer.getByRole('status')).toContainText('lesson.reject')

  const probe = server().probeStore()
  expect(probe.lessons[lessonIds.promote]).toMatchObject({
    confidence: 0.812,
    status: 'active',
  })
  expect(probe.lessons[lessonIds.reject]).toMatchObject({
    confidence: null,
    status: 'archived',
  })
  expect(targetSuccessReceipts(probe, lessonIds.promote)).toHaveLength(1)
  expect(targetSuccessReceipts(probe, lessonIds.reject)).toHaveLength(1)
  for (const receipt of [
    ...targetSuccessReceipts(probe, lessonIds.promote),
    ...targetSuccessReceipts(probe, lessonIds.reject),
  ]) {
    expect(receipt.actor).toEqual({ id: 'operator', type: 'human' })
  }
  expect(probe.context_lesson_ids).toContain(lessonIds.promote)
  expect(probe.context_lesson_ids).not.toContain(lessonIds.reject)
})

test('hostile HTTP envelopes, expired and revoked sessions all preserve zero action success', async ({
  request,
}) => {
  const baseline = server().probeStore()
  const baselineReceiptCount = baseline.receipts.length
  const commandUrl = `${apiBaseURL}/api/v1/lessons/${lessonIds.hostile}/promote`
  const unauthenticated = await request.post(commandUrl, {
    data: { confidence: 0.5 },
    headers: {
      Authorization: `Bearer ${E2E_OPERATOR_TOKEN}`,
      'Content-Type': 'application/json',
      'Idempotency-Key': 'hostile-unauthenticated',
      Origin: apiBaseURL,
      'X-Atlas-Actor': actorInjectionCanary,
    },
  })
  expect(unauthenticated.status()).toBe(401)
  responseErrors.push(await unauthenticated.text())

  const csrfToken = await apiSignIn(request)
  const baseHeaders = mutationHeaders(csrfToken, 'hostile-envelope')
  const cases: Array<{
    expected: number
    response: Promise<APIResponse>
  }> = [
    {
      expected: 403,
      response: request.post(commandUrl, {
        data: { confidence: 0.5 },
        headers: { ...baseHeaders, Origin: 'http://evil.test' },
      }),
    },
    {
      expected: 403,
      response: request.post(commandUrl, {
        data: { confidence: 0.5 },
        headers: {
          ...baseHeaders,
          Host: 'evil.test',
          Origin: 'http://evil.test',
        },
      }),
    },
    {
      expected: 403,
      response: request.post(commandUrl, {
        data: { confidence: 0.5 },
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': 'missing-csrf',
          Origin: apiBaseURL,
        },
      }),
    },
    {
      expected: 403,
      response: request.post(commandUrl, {
        data: { confidence: 0.5 },
        headers: { ...baseHeaders, 'X-Atlas-CSRF': 'wrong-csrf-canary' },
      }),
    },
    {
      expected: 415,
      response: request.post(commandUrl, {
        form: { confidence: '0.5' },
        headers: {
          'Idempotency-Key': 'simple-form',
          Origin: apiBaseURL,
          'X-Atlas-CSRF': csrfToken,
        },
      }),
    },
    {
      expected: 415,
      response: request.post(commandUrl, {
        data: { confidence: 0.5 },
        headers: {
          ...baseHeaders,
          'Content-Type': 'application/json; charset=utf-8',
        },
      }),
    },
    {
      expected: 422,
      response: request.post(commandUrl, {
        data: { actor: actorInjectionCanary, confidence: 0.5 },
        headers: {
          ...baseHeaders,
          'X-Atlas-Actor': actorInjectionCanary,
          'X-Atlas-Created-By-Type': 'agent',
        },
      }),
    },
  ]

  for (const item of cases) {
    const response = await item.response
    expect(response.status()).toBe(item.expected)
    responseErrors.push(await response.text())
  }

  server().setClock('2099-08-11T12:31:00+00:00')
  const expired = await request.post(commandUrl, {
    data: { confidence: 0.5 },
    headers: mutationHeaders(csrfToken, 'expired-session'),
  })
  expect(expired.status()).toBe(401)
  responseErrors.push(await expired.text())

  const revokedCsrf = await apiSignIn(request)
  const revoked = await request.delete(`${apiBaseURL}/api/v1/session`, {
    headers: {
      'Content-Type': 'application/json',
      Origin: apiBaseURL,
      'X-Atlas-CSRF': revokedCsrf,
    },
  })
  expect(revoked.status()).toBe(200)
  const afterRevoke = await request.post(commandUrl, {
    data: { confidence: 0.5 },
    headers: mutationHeaders(revokedCsrf, 'revoked-session'),
  })
  expect(afterRevoke.status()).toBe(401)
  responseErrors.push(await afterRevoke.text())

  const after = server().probeStore()
  expect(after.lessons[lessonIds.hostile].status).toBe('draft')
  expect(after.receipts).toHaveLength(baselineReceiptCount)
  expect(targetSuccessReceipts(after, lessonIds.hostile)).toHaveLength(0)
  for (const error of responseErrors) {
    expectSecretFree(error)
  }
})

test('duplicate submission, same-key replay and altered replay produce one transition and typed results', async ({
  request,
}) => {
  const csrfToken = await apiSignIn(request)
  const idempotencyKey = 'canary-same-command-key-atl-424'
  secretCanaries.add(idempotencyKey)
  const url = `${apiBaseURL}/api/v1/lessons/${lessonIds.replay}/promote`
  const submit = () =>
    request.post(url, {
      data: { confidence: 0.625 },
      headers: mutationHeaders(csrfToken, idempotencyKey),
    })

  const duplicateResponses = await Promise.all([submit(), submit()])
  const duplicateStatuses = duplicateResponses.map((response) =>
    response.status()
  )
  expect(duplicateStatuses.every((status) => [200, 409].includes(status))).toBe(
    true
  )
  const firstSuccess = duplicateResponses.find(
    (response) => response.status() === 200
  )
  if (!firstSuccess) {
    throw new Error('duplicate submission did not return a success response')
  }
  const replay = await submit()
  expect(replay.status()).toBe(200)
  expect(await replay.body()).toEqual(await firstSuccess.body())

  const altered = await request.post(url, {
    data: { confidence: 0.75 },
    headers: mutationHeaders(csrfToken, idempotencyKey),
  })
  expect(altered.status()).toBe(409)
  expect(await altered.json()).toEqual({
    detail: 'idempotency key conflicts with an existing command',
    lesson: null,
  })

  const probe = server().probeStore()
  expect(probe.lessons[lessonIds.replay]).toMatchObject({
    confidence: 0.625,
    status: 'active',
  })
  expect(targetSuccessReceipts(probe, lessonIds.replay)).toHaveLength(1)
  expectSecretFree(JSON.stringify(probe))
})

test('an ambiguous browser response retains its exact key and replays the original success', async ({
  page,
}) => {
  watchBrowserOutput(page)
  const commandPattern = `**/api/v1/lessons/${lessonIds.replayUx}/promote`
  let injectedFailure = false
  await page.route(commandPattern, async (route) => {
    if (injectedFailure) {
      await route.continue()
      return
    }
    injectedFailure = true
    const committedResponse = await route.fetch()
    expect(committedResponse.status()).toBe(200)
    await route.abort('failed')
  })

  await page.goto('/lessons')
  await pageSignIn(page)
  const drawer = await openLesson(page, lessonTitles.replayUx)
  await drawer.getByRole('button', { name: 'Promote' }).click()
  const confirmation = page.getByRole('alertdialog', {
    name: 'Confirm lesson promotion',
  })
  await confirmation
    .getByLabel('Operator confidence (0.0–1.0)')
    .fill('0.71')
  await confirmation.getByRole('button', { name: 'Confirm promotion' }).click()
  await expect(drawer.getByText(/did not return an unambiguous result/)).toBeVisible()

  await page.unroute(commandPattern)
  await drawer.getByRole('button', { name: 'Retry safely' }).click()
  await expect(drawer.getByRole('status')).toContainText('lesson.promote')
  await expect(drawer.getByText('Active', { exact: true }).first()).toBeVisible()
  expect(targetSuccessReceipts(server().probeStore(), lessonIds.replayUx)).toHaveLength(
    1
  )
})

test('an actual CLI ruling between browser review and submit wins exactly once with typed conflict UX', async ({
  page,
}) => {
  watchBrowserOutput(page)
  await page.goto('/lessons')
  await pageSignIn(page)
  const drawer = await openLesson(page, lessonTitles.cliRace)

  const cli = server().runCli(['lessons', 'reject', lessonIds.cliRace])
  expect(cli.status, cli.stderr).toBe(0)
  expect(cli.stdout).toContain('status is ARCHIVED')

  await drawer.getByRole('button', { name: 'Promote' }).click()
  const confirmation = page.getByRole('alertdialog', {
    name: 'Confirm lesson promotion',
  })
  await confirmation
    .getByLabel('Operator confidence (0.0–1.0)')
    .fill('0.52')
  await confirmation.getByRole('button', { name: 'Confirm promotion' }).click()
  await expect(drawer.getByText('Lesson changed; ruling blocked')).toBeVisible()
  await expect(drawer.getByText(/Safe current state: archived/)).toBeVisible()

  const probe = server().probeStore()
  expect(probe.lessons[lessonIds.cliRace].status).toBe('archived')
  expect(targetSuccessReceipts(probe, lessonIds.cliRace)).toHaveLength(1)
})

test('two independent browser tabs racing opposite rulings commit one transition and one receipt', async ({
  browser,
}) => {
  await server().restart({ receiptFailure: false })
  const firstContext = await browser.newContext({ baseURL: appBaseURL })
  const secondContext = await browser.newContext({ baseURL: appBaseURL })
  const first = await firstContext.newPage()
  const second = await secondContext.newPage()
  watchBrowserOutput(first)
  watchBrowserOutput(second)

  try {
    await Promise.all([first.goto('/lessons'), second.goto('/lessons')])
    await Promise.all([pageSignIn(first), pageSignIn(second)])
    const [firstDrawer, secondDrawer] = await Promise.all([
      openLesson(first, lessonTitles.twoTabs),
      openLesson(second, lessonTitles.twoTabs),
    ])
    await Promise.all([
      firstDrawer.getByRole('button', { name: 'Promote' }).click(),
      secondDrawer.getByRole('button', { name: 'Reject' }).click(),
    ])
    const firstConfirmation = first.getByRole('alertdialog', {
      name: 'Confirm lesson promotion',
    })
    await firstConfirmation
      .getByLabel('Operator confidence (0.0–1.0)')
      .fill('0.61')
    const secondConfirmation = second.getByRole('alertdialog', {
      name: 'Confirm lesson rejection',
    })

    await Promise.all([
      firstConfirmation
        .getByRole('button', { name: 'Confirm promotion' })
        .click(),
      secondConfirmation
        .getByRole('button', { name: 'Confirm rejection' })
        .click(),
    ])

    await expect
      .poll(async () => {
        const successes =
          (await firstDrawer.getByRole('status').count()) +
          (await secondDrawer.getByRole('status').count())
        const conflicts =
          (await firstDrawer
            .getByText('Lesson changed; ruling blocked')
            .count()) +
          (await secondDrawer
            .getByText('Lesson changed; ruling blocked')
            .count())
        return successes + conflicts
      })
      .toBe(2)
    const successCount =
      (await firstDrawer.getByRole('status').count()) +
      (await secondDrawer.getByRole('status').count())
    const conflictCount =
      (await firstDrawer.getByText('Lesson changed; ruling blocked').count()) +
      (await secondDrawer.getByText('Lesson changed; ruling blocked').count())
    expect(successCount).toBe(1)
    expect(conflictCount).toBe(1)

    const probe = server().probeStore()
    expect(['active', 'archived']).toContain(
      probe.lessons[lessonIds.twoTabs].status
    )
    expect(targetSuccessReceipts(probe, lessonIds.twoTabs)).toHaveLength(1)
  } finally {
    await Promise.all([firstContext.close(), secondContext.close()])
  }
})

test('receipt failure rolls back live state and restart exposes no unaudited success before explicit retry', async ({
  page,
  request,
}) => {
  await server().restart({
    receiptFailure: true,
    receiptFailureCanary,
  })
  watchBrowserOutput(page)
  let commandKey = ''
  page.on('request', (requestEvent) => {
    if (
      requestEvent.method() === 'POST' &&
      requestEvent.url().endsWith(`/${lessonIds.receiptFailure}/promote`)
    ) {
      commandKey = requestEvent.headers()['idempotency-key'] ?? ''
    }
  })

  await page.goto('/lessons')
  await pageSignIn(page)
  const drawer = await openLesson(page, lessonTitles.receiptFailure)
  await drawer.getByRole('button', { name: 'Promote' }).click()
  const confirmation = page.getByRole('alertdialog', {
    name: 'Confirm lesson promotion',
  })
  await confirmation
    .getByLabel('Operator confidence (0.0–1.0)')
    .fill('0.44')
  await confirmation.getByRole('button', { name: 'Confirm promotion' }).click()
  await expect(drawer.getByText(/did not return an unambiguous result/)).toBeVisible()
  expect(commandKey).not.toBe('')
  secretCanaries.add(commandKey)

  const failedProbe = server().probeStore()
  expect(failedProbe.lessons[lessonIds.receiptFailure].status).toBe('draft')
  expect(targetSuccessReceipts(failedProbe, lessonIds.receiptFailure)).toHaveLength(
    0
  )
  expectSecretFree(JSON.stringify(failedProbe))
  expect(server().output()).not.toContain(receiptFailureCanary)

  await server().restart({ receiptFailure: false })
  const afterRestart = server().probeStore()
  expect(afterRestart.lessons[lessonIds.receiptFailure].status).toBe('draft')
  expect(targetSuccessReceipts(afterRestart, lessonIds.receiptFailure)).toHaveLength(
    0
  )

  const csrfToken = await apiSignIn(request)
  const retry = await request.post(
    `${apiBaseURL}/api/v1/lessons/${lessonIds.receiptFailure}/promote`,
    {
      data: { confidence: 0.44 },
      headers: mutationHeaders(csrfToken, commandKey),
    }
  )
  expect(retry.status()).toBe(200)
  expectSecretFree(await retry.text())
  const retriedProbe = server().probeStore()
  expect(retriedProbe.lessons[lessonIds.receiptFailure]).toMatchObject({
    confidence: 0.44,
    status: 'active',
  })
  expect(targetSuccessReceipts(retriedProbe, lessonIds.receiptFailure)).toHaveLength(
    1
  )
})

test('credentials, CSRF and actor canaries remain absent from storage, URLs, assets, output and retained artifacts', async ({
  page,
  request,
}, testInfo) => {
  watchBrowserOutput(page)
  await page.goto('/lessons')
  const csrfToken = await pageSignIn(page)
  const storageAndUrl = await page.evaluate(() => ({
    cookies: document.cookie,
    local: JSON.stringify(window.localStorage),
    session: JSON.stringify(window.sessionStorage),
    url: window.location.href,
  }))
  expectSecretFree(JSON.stringify(storageAndUrl))

  const scriptUrls = await page.evaluate(() =>
    performance
      .getEntriesByType('resource')
      .map((entry) => entry.name)
      .filter((url) => /\.(?:js|tsx?)(?:\?|$)/.test(url))
  )
  for (const scriptUrl of scriptUrls) {
    const source = await request.get(scriptUrl)
    expectSecretFree(await source.text())
  }

  const probe = server().probeStore()
  expectSecretFree(JSON.stringify(probe))
  expectSecretFree(JSON.stringify(responseErrors))
  expectSecretFree(JSON.stringify(browserOutput))
  expectSecretFree(server().output())
  expect(browserOutput.every((value) => !value.includes('?token='))).toBe(true)
  expect(testInfo.attachments).toEqual([])
  expect(testInfo.project.use.trace ?? 'off').toBe('off')
  expect(testInfo.project.use.screenshot ?? 'off').toBe('off')
  expect(testInfo.project.use.video ?? 'off').toBe('off')
  expect(csrfToken).not.toBe('')
})
