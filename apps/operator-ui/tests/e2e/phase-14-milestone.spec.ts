import AxeBuilder from '@axe-core/playwright'
import {
  expect,
  test,
  type APIRequestContext,
  type APIResponse,
  type Page,
} from '@playwright/test'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  E2E_OPERATOR_TOKEN,
  startAtlasApiServer,
  type AtlasApiServer,
  type AtlasStoreProbe,
} from './atlas-api-server'

const seedPath = join(
  dirname(fileURLToPath(import.meta.url)),
  'fixtures',
  'phase-14-milestone-seed.json'
)
const apiBaseURL =
  process.env.ATLAS_OPERATOR_E2E_API_URL ?? 'http://127.0.0.1:18000'
const appPort = Number(process.env.ATLAS_OPERATOR_UI_E2E_PORT ?? 4173)
const appBaseURL = `http://127.0.0.1:${appPort}`
const apiOrigin = new URL(apiBaseURL).origin
const exactHead = 'a'.repeat(40)
const oldHead = 'e'.repeat(40)
const rawEvidenceCanary = 'PHASE14_RAW_EVIDENCE_SECRET_CANARY_7f9c'
const errorCanary = 'PHASE14_UNBOUNDED_ERROR_CANARY_d4b1'
const ticketKey = 'ATLAS-244'
const prNumber = 412

type AcceptanceSession = {
  criteria_fingerprint: string
  criteria_snapshot: Array<{ criterion_index: number; ticket_key: string }>
  lifecycle: string
  session_id: string
}

type AcceptanceResponse = {
  merge_ready: boolean
  reasons: string[]
  session: AcceptanceSession
}

type OperatorAuth = { csrf: string }

let apiServer: AtlasApiServer | undefined

test.describe.configure({ mode: 'serial' })

async function startFreshServer(): Promise<AtlasApiServer> {
  await apiServer?.stop()
  apiServer = await startAtlasApiServer({
    acceptance: true,
    clock: '2030-08-12T12:00:00+00:00',
    seedPath,
  })
  apiServer.setAcceptanceState({ github: 'current' })
  return apiServer
}

function server(): AtlasApiServer {
  if (!apiServer) throw new Error('Phase 14 API server is not running')
  return apiServer
}

test.beforeEach(async () => {
  await startFreshServer()
})

test.afterEach(async () => {
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

async function openPanel(page: Page): Promise<void> {
  await page.goto(`/reviews/${ticketKey}/acceptance`)
  await signIn(page)
  await expect(page.getByLabel('Repository')).toHaveValue('acme/atlas')
  await expect(page.getByLabel('Pull request number')).toHaveValue(String(prNumber))
}

async function createSessionInUI(page: Page): Promise<string> {
  await openPanel(page)
  await page.getByRole('button', { name: 'Create exact-head session' }).click()
  await expect(page.getByText('Preflight Passed', { exact: true }).first()).toBeVisible()
  await expect(page.locator('h1')).toBeFocused()
  const value = await page
    .locator('dt')
    .filter({ hasText: /^Session ID$/ })
    .locator('..')
    .locator('dd')
    .textContent()
  expect(value).toMatch(/^[0-9a-f-]{36}$/)
  return value as string
}

async function loadSessionInUI(page: Page, sessionId: string): Promise<void> {
  await openPanel(page)
  await page.getByLabel('Session ID').fill(sessionId)
  await page.getByRole('button', { name: 'Load session with fresh GET' }).click()
}

async function apiLogin(request: APIRequestContext): Promise<OperatorAuth> {
  const response = await request.post(`${apiBaseURL}/api/v1/session`, {
    data: { token: E2E_OPERATOR_TOKEN },
  })
  expect(response.status(), await response.text()).toBe(200)
  const body = (await response.json()) as { csrf_token: string }
  return { csrf: body.csrf_token }
}

function commandHeaders(auth: OperatorAuth, key: string): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    'Idempotency-Key': key,
    Origin: apiOrigin,
    'X-Atlas-CSRF': auth.csrf,
  }
}

async function expectJson(response: APIResponse): Promise<Record<string, unknown>> {
  return (await response.json()) as Record<string, unknown>
}

async function createSession(
  request: APIRequestContext,
  auth: OperatorAuth,
  key: string = crypto.randomUUID()
): Promise<AcceptanceSession> {
  const response = await request.post(
    `${apiBaseURL}/api/v1/reviews/${prNumber}/acceptance-sessions`,
    {
      data: { repository: 'acme/atlas' },
      headers: commandHeaders(auth, key),
    }
  )
  expect(response.status(), await response.text()).toBe(200)
  return ((await response.json()) as { session: AcceptanceSession }).session
}

async function pullEvidence(
  request: APIRequestContext,
  auth: OperatorAuth,
  session: AcceptanceSession,
  key: string = crypto.randomUUID()
): Promise<APIResponse> {
  return request.post(
    `${apiBaseURL}/api/v1/acceptance-sessions/${session.session_id}/evidence`,
    { data: {}, headers: commandHeaders(auth, key) }
  )
}

async function confirmSession(
  request: APIRequestContext,
  auth: OperatorAuth,
  session: AcceptanceSession,
  key: string = crypto.randomUUID()
): Promise<APIResponse> {
  return request.post(
    `${apiBaseURL}/api/v1/acceptance-sessions/${session.session_id}/confirm`,
    {
      data: {
        criteria_fingerprint: session.criteria_fingerprint,
        criterion_indexes: session.criteria_snapshot.map((_item, index) => index),
        manual_approval: true,
      },
      headers: commandHeaders(auth, key),
    }
  )
}

async function verifySession(
  request: APIRequestContext,
  auth: OperatorAuth,
  session: AcceptanceSession,
  key: string = crypto.randomUUID()
): Promise<APIResponse> {
  return request.post(
    `${apiBaseURL}/api/v1/acceptance-sessions/${session.session_id}/verify`,
    { data: {}, headers: commandHeaders(auth, key) }
  )
}

async function readSession(
  request: APIRequestContext,
  session: AcceptanceSession
): Promise<APIResponse> {
  return request.get(
    `${apiBaseURL}/api/v1/acceptance-sessions/${session.session_id}`
  )
}

async function advanceToEvidence(
  request: APIRequestContext,
  auth: OperatorAuth,
  session: AcceptanceSession
): Promise<void> {
  const response = await pullEvidence(request, auth, session)
  expect(response.status(), await response.text()).toBe(200)
}

async function advanceToConfirmations(
  request: APIRequestContext,
  auth: OperatorAuth,
  session: AcceptanceSession
): Promise<void> {
  await advanceToEvidence(request, auth, session)
  const response = await confirmSession(request, auth, session)
  expect(response.status(), await response.text()).toBe(200)
}

async function advanceToReady(
  request: APIRequestContext,
  auth: OperatorAuth,
  session: AcceptanceSession
): Promise<void> {
  await advanceToConfirmations(request, auth, session)
  const response = await verifySession(request, auth, session)
  expect(response.status(), await response.text()).toBe(200)
  expect(((await response.json()) as AcceptanceResponse).merge_ready).toBe(true)
}

function sessionProbe(probe: AtlasStoreProbe, sessionId: string) {
  const found = probe.acceptance_sessions.find((item) => item.id === sessionId)
  if (!found) throw new Error(`Missing acceptance session ${sessionId} in probe`)
  return found
}

function expectNoCanary(value: unknown): void {
  const retained = JSON.stringify(value)
  expect(retained).not.toContain(E2E_OPERATOR_TOKEN)
  expect(retained).not.toContain(rawEvidenceCanary)
  expect(retained).not.toContain(errorCanary)
  expect(retained.toLowerCase()).not.toContain('csrf_token')
  expect(retained.toLowerCase()).not.toContain('raw_payload')
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const offenders = await page.locator('body *').evaluateAll((elements) =>
    elements
      .filter((element) => {
        const style = window.getComputedStyle(element)
        const box = element.getBoundingClientRect()
        return (
          style.display !== 'none' &&
          style.visibility !== 'hidden' &&
          box.width > 0 &&
          box.height > 0 &&
          ['auto', 'scroll'].includes(style.overflowX) &&
          element.scrollWidth > element.clientWidth + 1
        )
      })
      .map((element) => ({
        label: `${element.tagName.toLowerCase()} ${element.textContent?.slice(0, 80)}`,
        width: `${element.clientWidth}/${element.scrollWidth}`,
      }))
  )
  expect(offenders).toEqual([])
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1
    )
  ).toBe(true)
}

test('built UI reaches exact-head readiness with one owner and zero external mutation', async ({
  page,
}) => {
  test.setTimeout(120_000)
  const acceptanceBodies: string[] = []
  let evidencePosts = 0
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      /\/acceptance-sessions\/[0-9a-f-]+\/evidence$/.test(request.url())
    ) {
      evidencePosts += 1
    }
  })
  page.on('response', async (response) => {
    if (response.url().includes('/acceptance-sessions')) {
      acceptanceBodies.push(await response.text())
    }
  })

  const before = server().probeStore()
  expect(before.acceptance_sessions).toEqual([])
  expect(before.evidence.map((item) => item.commit_sha)).toEqual([
    oldHead,
    oldHead,
    oldHead,
    oldHead,
  ])

  const sessionId = await createSessionInUI(page)
  await expect(page.getByText(exactHead, { exact: true }).first()).toBeVisible()
  await expect(page.getByText(/phase-14-exact-head-/).first()).toBeVisible()

  const evidenceButton = page.getByRole('button', { name: 'Pull evidence' })
  await evidenceButton.evaluate((element) => {
    const button = element as HTMLButtonElement
    button.click()
    button.click()
  })
  await expect(page.getByText('Evidence Ready', { exact: true }).first()).toBeVisible()
  expect(evidencePosts).toBe(1)
  await expect(page.locator('h1')).toBeFocused()
  await expect(page.getByRole('status')).toContainText('action_succeeded')

  const checkboxes = page.getByRole('checkbox')
  await expect(checkboxes).toHaveCount(4)
  await checkboxes.first().focus()
  for (let index = 0; index < 4; index += 1) {
    await page.keyboard.press('Space')
    if (index < 3) await page.keyboard.press('Tab')
  }
  await page.getByRole('button', { name: 'Confirm every criterion' }).click()
  await expect(
    page.getByText('Confirmations Ready', { exact: true }).first()
  ).toBeVisible()
  await expect(page.locator('h1')).toBeFocused()

  await page.getByRole('button', { name: 'Run verification' }).click()
  const ready = page.getByText('Exact verified head is ready for manual merge')
  await expect(ready).toBeVisible()
  await expect(ready.locator('..')).toContainText(exactHead)
  await expect(ready.locator('..')).toContainText('manually in GitHub')
  await expect(
    page.locator('dt').filter({ hasText: /^Top-level verdict$/ }).locator('..')
  ).toContainText('Passed')
  const matrix = page.getByRole('region', {
    name: `Canonical verification matrix for ${ticketKey}`,
  })
  expect(await matrix.getByTestId('review-check-row').count()).toBeGreaterThanOrEqual(6)

  for (const viewport of [
    { height: 768, width: 1366 },
    { height: 768, width: 1024 },
    { height: 844, width: 390 },
  ]) {
    await page.setViewportSize(viewport)
    await expectNoHorizontalOverflow(page)
  }
  const axe = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  expect(axe.violations).toEqual([])

  const after = server().probeStore()
  const stored = sessionProbe(after, sessionId)
  expect(stored.lifecycle).toBe('merge_ready')
  expect(stored.stored_merge_ready).toBe(true)
  expect(stored.step_summaries.verification.verification).toMatchObject({
    head_commit: exactHead,
    status: 'passed',
  })
  expect(after.ticket_statuses[ticketKey]).toBe('review_required')
  expect(after.ticket_transitions).toEqual([])
  expect(after.pm_sync_receipts).toEqual(before.pm_sync_receipts)
  expect(after.schema).toEqual(before.schema)
  expect(after.evidence.some((item) => item.type === 'merge')).toBe(false)
  expect(after.receipts.map((receipt) => receipt.action).sort()).toEqual([
    'acceptance_session.confirm',
    'acceptance_session.pull_evidence',
    'acceptance_session.verify',
  ])
  expect(after.receipts.every((receipt) => receipt.outcome === 'succeeded')).toBe(true)
  expect(server().externalMutations()).toEqual([])
  expectNoCanary(after)
  expectNoCanary(acceptanceBodies)
  expectNoCanary(await page.locator('body').innerText())
  expectNoCanary(server().output())
})

test('head and main movement fail closed at every seam and preserve post-PASSED history', async ({
  page,
  request,
}) => {
  test.setTimeout(300_000)
  const seams = [
    'before-evidence',
    'after-evidence',
    'before-confirmation',
    'before-verification',
    'after-passed',
  ] as const
  const movements = [
    { direct: 'head-moved', expected: 'head_sha_mismatch', kind: 'head' },
    { direct: 'main-moved', expected: 'base_sha_mismatch', kind: 'main' },
  ] as const

  let first = true
  for (const seam of seams) {
    for (const movement of movements) {
      if (!first) await startFreshServer()
      first = false
      const auth = await apiLogin(request)
      const session = await createSession(request, auth)
      let blocked: APIResponse

      if (seam === 'before-evidence') {
        server().setAcceptanceState({ github: movement.direct })
        blocked = await pullEvidence(request, auth, session)
      } else if (seam === 'after-evidence') {
        server().setAcceptanceState({
          github:
            movement.kind === 'head'
              ? 'head-moved-after-evidence'
              : 'main-moved-after-evidence',
        })
        blocked = await pullEvidence(request, auth, session)
      } else if (seam === 'before-confirmation') {
        await advanceToEvidence(request, auth, session)
        server().setAcceptanceState({ github: movement.direct })
        blocked = await confirmSession(request, auth, session)
      } else if (seam === 'before-verification') {
        await advanceToConfirmations(request, auth, session)
        server().setAcceptanceState({ github: movement.direct })
        blocked = await verifySession(request, auth, session)
      } else {
        await advanceToReady(request, auth, session)
        const beforeRead = server().probeStore()
        server().setAcceptanceState({ github: movement.direct })
        const response = await readSession(request, session)
        expect(response.status(), await response.text()).toBe(200)
        const body = (await response.json()) as AcceptanceResponse
        expect(body.merge_ready).toBe(false)
        expect(body.reasons).toContain(movement.expected)
        expect(new Set(body.reasons).size).toBe(body.reasons.length)
        const afterRead = server().probeStore()
        expect(sessionProbe(afterRead, session.session_id)).toEqual(
          sessionProbe(beforeRead, session.session_id)
        )
        expect(afterRead.receipts).toEqual(beforeRead.receipts)
        expect(afterRead.verification_checks).toEqual(beforeRead.verification_checks)
        if (movement.kind === 'head') {
          await loadSessionInUI(page, session.session_id)
          await expect(page.getByText('Live merge readiness is closed')).toBeVisible()
          await expect(page.getByText(/Head Sha Mismatch/)).toBeVisible()
        }
        continue
      }

      expect(blocked.status(), await blocked.text()).toBeGreaterThanOrEqual(400)
      const body = await expectJson(blocked)
      const reasons = body.reasons as string[]
      expect(reasons).toContain(movement.expected)
      expect(reasons.length).toBeGreaterThan(0)
      expect(new Set(reasons).size).toBe(reasons.length)
      const probe = server().probeStore()
      const stored = sessionProbe(probe, session.session_id)
      expect(stored.lifecycle).toBe('stale')
      expect(stored.stored_merge_ready).toBe(false)

      const reuse = await verifySession(request, auth, session)
      expect(reuse.status()).toBeGreaterThanOrEqual(400)
      expect(sessionProbe(server().probeStore(), session.session_id).stored_merge_ready).toBe(
        false
      )
    }
  }
})

test('criteria drift, old-head gates, and every non-PASSED verdict remain non-ready in API and UI', async ({
  page,
  request,
}) => {
  test.setTimeout(300_000)
  let auth = await apiLogin(request)
  let session = await createSession(request, auth)
  await advanceToEvidence(request, auth, session)
  server().setAcceptanceState({ ticket: 'criteria-drift' })
  const drifted = await confirmSession(request, auth, session)
  expect(drifted.status(), await drifted.text()).toBe(409)
  expect((await expectJson(drifted)).reasons).toContain('criteria_mismatch')
  await loadSessionInUI(page, session.session_id)
  await expect(page.getByText(/Criteria Mismatch/).first()).toBeVisible()
  expect(sessionProbe(server().probeStore(), session.session_id).stored_merge_ready).toBe(
    false
  )

  await startFreshServer()
  auth = await apiLogin(request)
  session = await createSession(request, auth)
  await advanceToEvidence(request, auth, session)
  const missingGates = await verifySession(request, auth, session)
  expect(missingGates.status()).toBeGreaterThanOrEqual(400)
  const missingReasons = (await expectJson(missingGates)).reasons as string[]
  expect(missingReasons).toEqual(
    expect.arrayContaining(['confirmations_not_ready', 'session_not_verifiable'])
  )
  const oldHeadEvidence = server()
    .probeStore()
    .evidence.filter((item) => item.commit_sha === oldHead)
  expect(oldHeadEvidence.some((item) => item.type === 'manual_approval')).toBe(true)
  expect(sessionProbe(server().probeStore(), session.session_id).stored_merge_ready).toBe(
    false
  )

  const verdicts = [
    ['pending', 'verification_pending'],
    ['failed', 'verification_failed'],
    ['warning', 'verification_warning'],
    ['not_applicable', 'verification_not_applicable'],
    ['old-head', 'verified_head_mismatch'],
    ['malformed', 'verification_malformed'],
    ['close-set-mismatch', 'verification_close_set_mismatch'],
  ] as const

  for (const [verification, expectedReason] of verdicts) {
    await startFreshServer()
    auth = await apiLogin(request)
    session = await createSession(request, auth)
    await advanceToConfirmations(request, auth, session)
    server().setAcceptanceState({ github: 'current', verification })
    const response = await verifySession(request, auth, session)
    expect(response.status(), await response.text()).toBeGreaterThanOrEqual(400)
    const reasons = (await expectJson(response)).reasons as string[]
    expect(reasons).toContain(expectedReason)
    expect(sessionProbe(server().probeStore(), session.session_id).stored_merge_ready).toBe(
      false
    )
    await loadSessionInUI(page, session.session_id)
    await expect(
      page.getByText(new RegExp(expectedReason.split('_').join(' '), 'i')).first()
    ).toBeVisible()
  }
})

test('same and altered replay plus concurrent browser transitions have one action owner', async ({
  browser,
  request,
}) => {
  test.setTimeout(180_000)
  let auth = await apiLogin(request)
  let session = await createSession(request, auth)
  const replayKey = 'phase-14-same-replay'
  const first = await pullEvidence(request, auth, session, replayKey)
  expect(first.status(), await first.text()).toBe(200)
  const firstBody = await expectJson(first)
  const same = await pullEvidence(request, auth, session, replayKey)
  expect(same.status(), await same.text()).toBe(200)
  const sameBody = await expectJson(same)
  expect(sameBody.receipt).toEqual(firstBody.receipt)
  expect(server().probeStore().receipts).toHaveLength(1)

  const altered = await verifySession(request, auth, session, replayKey)
  expect(altered.status(), await altered.text()).toBe(409)
  expect(await expectJson(altered)).toMatchObject({
    conflict_code: 'idempotency_key_reused',
  })
  expect(server().probeStore().receipts).toHaveLength(1)

  await startFreshServer()
  auth = await apiLogin(request)
  session = await createSession(request, auth)
  server().setAcceptanceState({ delay_ms: 100, github: 'current' })

  const contexts = await Promise.all([
    browser.newContext({ baseURL: appBaseURL }),
    browser.newContext({ baseURL: appBaseURL }),
  ])
  try {
    const pages = await Promise.all(contexts.map((context) => context.newPage()))
    await Promise.all(pages.map((page) => loadSessionInUI(page, session.session_id)))
    await Promise.all(
      pages.map((page) => page.getByRole('button', { name: 'Pull evidence' }).click())
    )
    await expect
      .poll(() =>
        server()
          .probeStore()
          .receipts.filter(
            (receipt) => receipt.action === 'acceptance_session.pull_evidence'
          ).length
      )
      .toBe(2)
    const probe = server().probeStore()
    const receipts = probe.receipts.filter(
      (receipt) => receipt.action === 'acceptance_session.pull_evidence'
    )
    expect(receipts.filter((receipt) => receipt.outcome === 'succeeded')).toHaveLength(1)
    expect(receipts.filter((receipt) => receipt.outcome === 'refused')).toHaveLength(1)
    expect(
      sessionProbe(probe, session.session_id).step_summaries.evidence.receipt_ids
    ).toHaveLength(1)
    expect(server().externalMutations()).toEqual([])
  } finally {
    await Promise.all(contexts.map((context) => context.close()))
  }
})

test('timeouts, malformed responses, receipt/store failure, and post-PASSED GitHub failure recover without unaudited success', async ({
  request,
}) => {
  test.setTimeout(240_000)
  const auth = await apiLogin(request)
  const session = await createSession(request, auth)
  const initial = sessionProbe(server().probeStore(), session.session_id)
  server().setAcceptanceState({
    error_canary: errorCanary,
    github: 'timeout',
  })
  let response = await pullEvidence(request, auth, session)
  expect(response.status(), await response.text()).toBe(504)
  expect((await expectJson(response)).reasons).toEqual(
    expect.arrayContaining(['external_read_timeout', 'external_state_indeterminate'])
  )
  expect(sessionProbe(server().probeStore(), session.session_id)).toEqual(initial)
  expectNoCanary(await response.text())

  server().setAcceptanceState({
    error_canary: errorCanary,
    github: 'evidence-malformed',
  })
  response = await pullEvidence(request, auth, session)
  expect(response.status(), await response.text()).toBe(502)
  expect(await expectJson(response)).toMatchObject({
    result_code: 'evidence_malformed_source',
  })
  expect(sessionProbe(server().probeStore(), session.session_id)).toEqual(initial)
  expectNoCanary(await response.text())

  server().setAcceptanceState({ github: 'current' })
  await advanceToEvidence(request, auth, session)
  const beforeStoreFailure = server().probeStore()
  server().setAcceptanceState({
    error_canary: errorCanary,
    github: 'current',
    store_failure: true,
  })
  response = await confirmSession(request, auth, session)
  expect(response.status(), await response.text()).toBe(500)
  expect(sessionProbe(server().probeStore(), session.session_id)).toEqual(
    sessionProbe(beforeStoreFailure, session.session_id)
  )
  expectNoCanary(await response.text())

  server().setAcceptanceState({ github: 'current' })
  response = await confirmSession(request, auth, session)
  expect(response.status(), await response.text()).toBe(200)
  const beforeReceiptFailure = server().probeStore()
  server().setAcceptanceState({
    error_canary: errorCanary,
    github: 'current',
    receipt_failure_action: 'acceptance_session.verify',
  })
  response = await verifySession(request, auth, session)
  expect(response.status(), await response.text()).toBe(500)
  const failedReceiptProbe = server().probeStore()
  expect(sessionProbe(failedReceiptProbe, session.session_id)).toEqual(
    sessionProbe(beforeReceiptFailure, session.session_id)
  )
  expect(sessionProbe(failedReceiptProbe, session.session_id).stored_merge_ready).toBe(
    false
  )
  expectNoCanary(await response.text())

  server().setAcceptanceState({ github: 'current' })
  response = await verifySession(request, auth, session)
  expect(response.status(), await response.text()).toBe(200)
  expect(((await response.json()) as AcceptanceResponse).merge_ready).toBe(true)

  const beforeFailedGet = server().probeStore()
  server().setAcceptanceState({
    error_canary: errorCanary,
    github: 'failure',
  })
  response = await readSession(request, session)
  expect(response.status(), await response.text()).toBe(200)
  const failedGet = (await response.json()) as AcceptanceResponse
  expect(failedGet.merge_ready).toBe(false)
  expect(failedGet.reasons).toEqual(
    expect.arrayContaining(['external_read_failed', 'external_state_indeterminate'])
  )
  expect(server().probeStore()).toEqual(beforeFailedGet)
  expectNoCanary(failedGet)
  expect(server().externalMutations()).toEqual([])
})
