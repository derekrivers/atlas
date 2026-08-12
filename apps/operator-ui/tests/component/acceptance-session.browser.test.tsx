import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { components } from '@/api/atlas-openapi'
import { atlasForgetSession } from '@/api/client'
import { createAtlasQueryClient } from '@/api/query-policy'
import { AppProviders } from '@/app-providers'
import { AcceptanceSessionPanel } from '@/features/reviews/acceptance-session-view'

type Schema = components['schemas']
type Session = Schema['AcceptanceSessionSchema']
type Lifecycle = Schema['AcceptanceSessionLifecycle']
type ReadResponse = Schema['AcceptanceSessionReadResponse']
type ActionReceipt = Schema['AcceptanceActionReceiptSchema']

const sessionId = '00000000-0000-4000-8000-000000000415'
const head = 'a'.repeat(40)
const base = 'b'.repeat(40)
const fingerprint = `sha256:${'c'.repeat(64)}`

const ticket: Schema['TicketDetailResponse'] = {
  acceptance_criteria: ['First criterion', 'Second criterion'],
  completed_at: null,
  component: 'operator-ui',
  context: 'Acceptance panel component context.',
  created_at: '2026-08-12T12:00:00+00:00',
  definition_of_done: ['The component contract passes.'],
  documentation_requirements: ['docs/atlas/operator-ui.md'],
  estimated_effort: 3,
  external_github_issue_id: '415',
  external_linear_id: 'ATL-415',
  implementation_notes: ['Use generated types.'],
  key: 'ATLAS-243',
  non_goals: ['No merge control.'],
  objective: 'Render the exact-head acceptance workflow.',
  priority: 1,
  relevant_docs: ['docs/atlas/operator-ui.md'],
  risk_level: 'high',
  source_anchor: 'docs/atlas/operator-ui.md#review-acceptance',
  status: 'review_required',
  tags: ['operator-ui', 'acceptance'],
  test_requirements: ['Component tests pass.'],
  ticket_type: 'feature',
  title: 'Review queue acceptance console UI',
  updated_at: '2026-08-12T12:00:00+00:00',
}

const review: Schema['ReviewQueueItemSchema'] = {
  checks: [
    { check_type: 'tests', status: 'passed' },
    { check_type: 'lint', status: 'passed' },
    { check_type: 'acceptance_criteria', status: 'passed' },
    { check_type: 'documentation', status: 'passed' },
    { check_type: 'scope', status: 'passed' },
    { check_type: 'human_approval', status: 'passed' },
    { check_type: 'security', status: 'not_applicable' },
  ],
  has_pr_merged_evidence: false,
  has_system_evidence: true,
  key: ticket.key,
  status: 'review_required',
  ticket_type: 'feature',
  title: ticket.title,
  verdict: 'passed',
}

const receipt: ActionReceipt = {
  action: 'acceptance_session.pull_evidence',
  actor: { id: 'operator', type: 'human' },
  after_status: null,
  before_status: null,
  completed_at: '2026-08-12T12:02:00+00:00',
  correlation_id: '00000000-0000-4000-8000-00000000c415',
  created_at: '2026-08-12T12:01:00+00:00',
  idempotency_key_identity: `sha256:${'d'.repeat(64)}`,
  outcome: 'succeeded',
  receipt_id: '00000000-0000-4000-8000-00000000e415',
  request_fingerprint: `sha256:${'e'.repeat(64)}`,
  result_code: 'action_succeeded',
  result_metadata: { affected_count: 2, changed: true },
  target: { id: sessionId, type: 'acceptance_session' },
}

function step(
  state: Schema['AcceptanceSessionStepState'],
  additions: Partial<Schema['AcceptanceStepSummary']> = {}
): Schema['AcceptanceStepSummary'] {
  return {
    occurred_at: state === 'complete' ? '2026-08-12T12:01:00+00:00' : null,
    reasons: [],
    receipt_ids: state === 'complete' ? [receipt.receipt_id] : [],
    state,
    ...additions,
  }
}

function sessionFor(lifecycle: Lifecycle): Session {
  const evidenceComplete = !['preflight_passed'].includes(lifecycle)
  const confirmationComplete = [
    'confirmations_ready',
    'verification_passed',
    'merge_ready',
  ].includes(lifecycle)
  const verificationComplete = ['verification_passed', 'merge_ready'].includes(
    lifecycle
  )
  const terminal = ['blocked', 'failed', 'stale'].includes(lifecycle)
  return {
    actor: { id: 'operator', type: 'human' },
    blocking_reasons: terminal ? ['session_stale'] : [],
    close_set: [ticket.key],
    criteria_fingerprint: fingerprint,
    criteria_snapshot: [
      { criterion_index: 4, text: 'First criterion', ticket_key: ticket.key },
      { criterion_index: 9, text: 'Second criterion', ticket_key: ticket.key },
    ],
    historical_readiness: {
      authority: 'historical_only',
      is_current_merge_authority: false,
      reasons: lifecycle === 'merge_ready' ? [] : ['verification_not_passed'],
      stored_merge_ready: lifecycle === 'merge_ready',
    },
    initial_assessment: {
      ahead_by: 1,
      ancestry: 'current',
      base_sha_source: 'live_branch',
      behind_by: 0,
      compare_status: 'ahead',
      eligibility: 'eligible',
      integration_status: 'current',
      merge_base_sha: base,
      mergeability: 'mergeable',
      pr_draft: false,
      pr_merged: false,
      pr_state: 'open',
    },
    lifecycle,
    pinned_identity: {
      base: { ref: 'main', repository: 'acme/atlas', sha: base },
      head: { ref: 'agent/atl-415', repository: 'acme/atlas', sha: head },
      pr_number: 415,
      repository: { name: 'atlas', owner: 'acme' },
    },
    receipts: [receipt.receipt_id],
    session_id: sessionId,
    steps: {
      preflight: step('complete'),
      evidence: step(evidenceComplete ? 'complete' : terminal ? 'blocked' : 'pending', {
        evidence: evidenceComplete
          ? {
              agent_count: 0,
              checks_count: 2,
              complete_pin_count: 2,
              docs_count: 0,
              exact_head_pin_complete: true,
              exact_head_pin_count: 2,
              failed_count: 0,
              human_count: confirmationComplete ? 3 : 0,
              latest_source_event_at: '2026-08-12T12:01:00+00:00',
              new_count: 2,
              not_applicable_count: 0,
              oldest_source_event_at: '2026-08-12T12:00:00+00:00',
              passed_count: 2,
              pending_count: 0,
              pin_complete: true,
              reviews_count: 0,
              system_count: 2,
              total_count: 2,
              warning_count: 0,
            }
          : null,
      }),
      confirmations: step(
        confirmationComplete ? 'complete' : terminal ? 'blocked' : 'pending'
      ),
      verification: step(
        verificationComplete ? 'complete' : terminal ? 'blocked' : 'pending',
        {
          verification: verificationComplete
            ? {
                blocking_check_count: 0,
                head_commit: head,
                status: 'passed',
                ticket_count: 1,
                verdict_id: '00000000-0000-4000-8000-00000000f415',
              }
            : null,
        }
      ),
      readiness: step(lifecycle === 'merge_ready' ? 'complete' : 'pending'),
    },
    timestamps: {
      created_at: '2026-08-12T12:00:00+00:00',
      staled_at: lifecycle === 'stale' ? '2026-08-12T12:03:00+00:00' : null,
      updated_at: '2026-08-12T12:02:00+00:00',
    },
  }
}

function readFor(lifecycle: Lifecycle, mergeReady = lifecycle === 'merge_ready'): ReadResponse {
  return {
    merge_ready: mergeReady,
    reasons: mergeReady ? [] : ['verification_not_passed'],
    session: sessionFor(lifecycle),
  }
}

function actionResponse(session: Session, action: ActionReceipt['action']) {
  return {
    merge_ready: action === 'acceptance_session.verify',
    receipt: { ...receipt, action },
    session,
  } satisfies Schema['AcceptanceSessionActionResponse']
}

let mountedRoot: Root | undefined
let container: HTMLDivElement | undefined
let originalFetch: typeof window.fetch

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'content-type': 'application/json' },
    status,
  })
}

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return `${input.pathname}${input.search}`
  const url = new URL(input.url, window.location.origin)
  return `${url.pathname}${url.search}`
}

async function waitForAssertion(assertion: () => void): Promise<void> {
  let lastError: unknown
  for (let attempt = 0; attempt < 150; attempt += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10))
    })
    try {
      assertion()
      return
    } catch (error) {
      lastError = error
    }
  }
  throw lastError
}

function button(name: string): HTMLButtonElement {
  const match = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(
    (item) => item.textContent?.trim() === name
  )
  if (!match) throw new Error(`Missing button: ${name}`)
  return match
}

async function click(name: string): Promise<void> {
  await act(async () => button(name).click())
}

function setInput(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    'value'
  )?.set
  setter?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

async function renderPanel(): Promise<void> {
  container = document.createElement('div')
  document.body.append(container)
  mountedRoot = createRoot(container)
  await act(async () => {
    mountedRoot?.render(
      <AppProviders queryClient={createAtlasQueryClient()}>
        <AcceptanceSessionPanel ticketKey={ticket.key} />
      </AppProviders>
    )
  })
  await waitForAssertion(() =>
    expect(document.body.textContent).toContain('Exact-head acceptance panel')
  )
}

async function login(): Promise<void> {
  const token = document.querySelector<HTMLInputElement>('#atlas-bootstrap-token')
  if (!token) throw new Error('Missing bootstrap token input')
  setInput(token, 'component-acceptance-bootstrap-token')
  await click('Sign in')
  await waitForAssertion(() =>
    expect(document.body.textContent).not.toContain('Operator sign in')
  )
}

async function loadSession(): Promise<void> {
  const input = document.querySelector<HTMLInputElement>('#acceptance-session-id')
  if (!input) throw new Error('Missing session ID input')
  setInput(input, sessionId)
  await click('Load session with fresh GET')
  await waitForAssertion(() =>
    expect(document.body.textContent).toMatch(/Operator sign in|Restore operator session/)
  )
  await login()
  await click('Load session with fresh GET')
  await waitForAssertion(() => {
    expect(document.body.textContent).toContain('Exact-head identity')
    expect(document.activeElement?.textContent).toContain('Exact-head acceptance panel')
  })
}

function standardFetch(read: () => ReadResponse): typeof window.fetch {
  return vi.fn(async (input, init) => {
    const path = requestPath(input)
    if (path === '/api/v1/session' && init?.method === 'POST') {
      return jsonResponse({
        authenticated: true,
        csrf_token: 'component-acceptance-csrf',
        expires_at: '2099-08-12T13:00:00+00:00',
      })
    }
    if (path === '/api/v1/session') {
      return jsonResponse({ authenticated: false, expires_at: null })
    }
    if (path === `/api/v1/tickets/${ticket.key}`) return jsonResponse(ticket)
    if (path === '/api/v1/reviews') return jsonResponse({ reviews: [review] })
    if (path === `/api/v1/acceptance-sessions/${sessionId}`) {
      return jsonResponse(read())
    }
    throw new Error(`Unexpected request: ${init?.method ?? 'GET'} ${path}`)
  }) as typeof window.fetch
}

beforeEach(() => {
  originalFetch = window.fetch
})

afterEach(() => {
  mountedRoot?.unmount()
  container?.remove()
  window.fetch = originalFetch
  atlasForgetSession()
  mountedRoot = undefined
  container = undefined
  window.localStorage.clear()
  window.sessionStorage.clear()
})

describe('acceptance session browser component', () => {
  it('runs the generated request sequence without local readiness derivation', async () => {
    let currentRead = readFor('preflight_passed')
    const requests: Array<{ body: string | null; headers: HeadersInit | undefined; path: string }> = []
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'component-acceptance-csrf',
          expires_at: '2099-08-12T13:00:00+00:00',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      if (path === `/api/v1/tickets/${ticket.key}`) return jsonResponse(ticket)
      if (path === '/api/v1/reviews') return jsonResponse({ reviews: [review] })
      if (path === `/api/v1/acceptance-sessions/${sessionId}`) {
        return jsonResponse(currentRead)
      }
      if (path === '/api/v1/reviews/415/acceptance-sessions') {
        requests.push({ body: String(init?.body), headers: init?.headers, path })
        return jsonResponse({
          receipt: {
            action: 'acceptance_session.create',
            actor: { id: 'operator', type: 'human' },
            completed_at: '2026-08-12T12:00:00+00:00',
            idempotency_key_identity: `sha256:${'1'.repeat(64)}`,
            outcome: 'created',
            target: { id: sessionId, type: 'acceptance_session' },
          },
          session: currentRead.session,
        })
      }
      if (path.endsWith('/evidence')) {
        requests.push({ body: String(init?.body), headers: init?.headers, path })
        currentRead = readFor('evidence_ready')
        return jsonResponse(
          actionResponse(
            currentRead.session,
            'acceptance_session.pull_evidence'
          )
        )
      }
      if (path.endsWith('/confirm')) {
        requests.push({ body: String(init?.body), headers: init?.headers, path })
        currentRead = readFor('confirmations_ready')
        return jsonResponse(
          actionResponse(currentRead.session, 'acceptance_session.confirm')
        )
      }
      if (path.endsWith('/verify')) {
        requests.push({ body: String(init?.body), headers: init?.headers, path })
        currentRead = readFor('merge_ready', false)
        return jsonResponse(
          actionResponse(currentRead.session, 'acceptance_session.verify')
        )
      }
      throw new Error(`Unexpected request: ${init?.method ?? 'GET'} ${path}`)
    }) as typeof window.fetch

    await renderPanel()
    const repository = document.querySelector<HTMLInputElement>(
      '#acceptance-repository'
    )
    if (!repository) throw new Error('Missing repository input')
    setInput(repository, 'acme/atlas')
    await click('Create exact-head session')
    await login()
    await click('Create exact-head session')

    await waitForAssertion(() =>
      expect(document.body.textContent).toContain('Pull exact-head evidence')
    )
    expect(requests[0].body).toBe(JSON.stringify({ repository: 'acme/atlas' }))
    expect((requests[0].headers as Record<string, string>)['X-Atlas-CSRF']).toBe(
      'component-acceptance-csrf'
    )
    expect(
      (requests[0].headers as Record<string, string>)['Idempotency-Key']
    ).toMatch(/^[0-9a-f-]{36}$/)
    expect(document.body.textContent).toContain(head)
    expect(document.body.textContent).toContain(base)

    await click('Pull evidence')
    await waitForAssertion(() =>
      expect(document.body.textContent).toContain('Server-snapshot criteria')
    )
    expect(requests[1].body).toBe('{}')

    const criterionInputs = Array.from(
      document.querySelectorAll<HTMLButtonElement>('[id^="criterion-"]')
    )
    expect(criterionInputs).toHaveLength(2)
    for (const checkbox of criterionInputs) {
      await act(async () => checkbox.click())
    }
    await act(async () =>
      document
        .querySelector<HTMLButtonElement>('[id^="manual-approval-"]')
        ?.click()
    )
    await click('Confirm every criterion')
    await waitForAssertion(() =>
      expect(document.body.textContent).toContain('Run canonical verification')
    )
    expect(JSON.parse(requests[2].body ?? '{}')).toEqual({
      criteria_fingerprint: fingerprint,
      criterion_indexes: [4, 9],
      manual_approval: true,
    })
    expect(requests[2].body).not.toContain('First criterion')

    await click('Run verification')
    await waitForAssertion(() => {
      expect(document.body.textContent).toContain('Top-level verdict')
      expect(document.body.textContent).toContain('Passed')
    })
    expect(document.body.textContent).not.toContain(
      'Merge this exact verified SHA manually in GitHub'
    )

    currentRead = readFor('merge_ready', true)
    await click('Refresh current state')
    await waitForAssertion(() =>
      expect(document.body.textContent).toContain(
        'Merge this exact verified SHA manually in GitHub'
      )
    )
    expect(document.body.textContent).toContain(head)

    const interactiveCopy = Array.from(
      document.querySelectorAll<HTMLButtonElement | HTMLAnchorElement>('button, a')
    )
      .map((item) => item.textContent?.trim().toLowerCase())
      .join(' ')
    for (const forbidden of [
      'auto-merge',
      'rebase',
      'linear status',
      'symphony resume',
      'schema upgrade',
      'pm sync',
    ]) {
      expect(interactiveCopy).not.toContain(forbidden)
    }
  })

  it.each([
    ['preflight_passed', 'Pull evidence'],
    ['evidence_ready', 'Confirm every criterion'],
    ['confirmations_ready', 'Run verification'],
    ['verification_passed', null],
    ['merge_ready', null],
    ['stale', null],
    ['blocked', null],
    ['failed', null],
  ] as const)(
    'renders lifecycle %s with only its server-sequenced action primary',
    async (lifecycle, expected) => {
      window.fetch = standardFetch(() => readFor(lifecycle))
      await renderPanel()
      await loadSession()

      const actionNames = ['Pull evidence', 'Confirm every criterion', 'Run verification']
      for (const actionName of actionNames) {
        const exists = Array.from(document.querySelectorAll('button')).some(
          (item) => item.textContent?.trim() === actionName
        )
        expect(exists).toBe(actionName === expected)
      }
      expect(
        document.querySelector(`[data-step-state="${
          lifecycle === 'failed' || lifecycle === 'blocked' || lifecycle === 'stale'
            ? 'blocked'
            : 'complete'
        }"]`)
      ).not.toBeNull()
    }
  )

  it.each([
    [403, { detail: 'Origin refused' }, 'security'],
    [
      409,
      {
        conflict_code: 'idempotency_key_reused',
        detail: 'Altered replay refused',
      },
      'replay-conflict',
    ],
    [
      409,
      {
        detail: 'Head moved',
        reasons: ['head_sha_mismatch'],
        result_code: 'stale_state',
      },
      'stale',
    ],
    [
      504,
      {
        detail: 'External read timed out',
        reasons: ['external_read_timeout', 'external_state_indeterminate'],
        result_code: 'external_timeout',
      },
      'timeout',
    ],
    [422, { detail: 'Action is out of order' }, 'blocked'],
    [404, { detail: 'Acceptance session not found' }, 'session-expired'],
  ] as const)(
    'announces HTTP %s as the distinct %s state',
    async (status, body, kind) => {
      const baseFetch = standardFetch(() => readFor('preflight_passed'))
      window.fetch = vi.fn(async (input, init) => {
        const path = requestPath(input)
        if (path.endsWith('/evidence')) return jsonResponse(body, status)
        return baseFetch(input, init)
      }) as typeof window.fetch
      await renderPanel()
      await loadSession()
      await click('Pull evidence')

      await waitForAssertion(() =>
        expect(document.querySelector(`[data-error-kind="${kind}"]`)).not.toBeNull()
      )
      const alert = document.querySelector(`[data-error-kind="${kind}"]`)
      expect(alert?.getAttribute('aria-live')).toBe('assertive')
      expect(document.activeElement?.contains(alert)).toBe(true)
    }
  )

  it('retains one idempotency key only for an ambiguous transport failure', async () => {
    const baseFetch = standardFetch(() => readFor('preflight_passed'))
    const keys: string[] = []
    let attempt = 0
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path.endsWith('/evidence')) {
        keys.push((init?.headers as Record<string, string>)['Idempotency-Key'])
        attempt += 1
        if (attempt === 1) throw new TypeError('seeded ambiguous network loss')
        return jsonResponse(
          actionResponse(sessionFor('evidence_ready'), 'acceptance_session.pull_evidence')
        )
      }
      return baseFetch(input, init)
    }) as typeof window.fetch
    await renderPanel()
    await loadSession()
    await click('Pull evidence')
    await waitForAssertion(() =>
      expect(document.body.textContent).toContain('Retry same command key')
    )
    await click('Retry same command key')
    await waitForAssertion(() => expect(keys).toHaveLength(2))
    expect(keys[0]).toBe(keys[1])
  })
})
