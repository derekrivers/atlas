import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import type { QueryClient } from '@tanstack/react-query'
import {
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { components } from '@/api/atlas-openapi'
import { atlasForgetSession } from '@/api/client'
import { createAtlasQueryClient } from '@/api/query-policy'
import { AppProviders } from '@/app-providers'
import { DeliveryControlView } from '@/features/delivery-control/delivery-control-view'

type Schema = components['schemas']
type DeliveryControl = Schema['DeliveryControlResponse']
type Mode = Schema['DeliveryAdmissionMode']
type Receipt = Schema['DeliveryPolicyActionReceiptSchema']

const longComponent =
  'operator-ui/admission-explanation-with-an-intentionally-long-responsive-component-name'

const receipt: Receipt = {
  action: 'delivery_admission_policy.revise',
  actor: { id: 'operator', type: 'human' },
  after_status: null,
  before_status: null,
  completed_at: '2026-08-13T10:05:00Z',
  correlation_id: '00000000-0000-4000-8000-00000000c251',
  created_at: '2026-08-13T10:05:00Z',
  idempotency_key_identity: `sha256:${'a'.repeat(64)}`,
  outcome: 'succeeded',
  receipt_id: '00000000-0000-4000-8000-00000000e251',
  request_fingerprint: `sha256:${'b'.repeat(64)}`,
  result_code: 'action_succeeded',
  result_metadata: { affected_count: 1, changed: true },
  target: { id: '00000000-0000-4000-8000-000000000388', type: 'product' },
}

function deliveryControl(mode: Mode = 'running', revision = 7): DeliveryControl {
  return {
    indeterminate_reasons: [
      {
        admission_run_id: '00000000-0000-4000-8000-000000000752',
        observed_at: '2026-08-13T10:03:00Z',
        policy_revision: revision,
        reason: 'write_indeterminate',
        state: 'indeterminate',
        ticket_key: 'ATLAS-752',
      },
    ],
    last_linear_sync_at: '2026-08-13T10:00:00Z',
    latest_admission: {
      decision_count: 2,
      decisions: [
        {
          decision: 'admit',
          protected_lanes: [],
          rank: 1,
          rank_inputs: {
            continuously_eligible_age_microseconds: 300_000_000,
            continuously_eligible_since: '2026-08-13T09:55:00Z',
            critical_path_member: true,
            critical_path_position: 2,
            priority: 1,
            risk_level: 'high',
            risk_severity: 2,
            unlock_count: 4,
          },
          reasons: [],
          ticket_key: 'ATLAS-751',
        },
        {
          decision: 'hold',
          protected_lane_registry_fingerprint: 'f'.repeat(64),
          protected_lane_registry_version: 'protected-integration-lanes/v1',
          protected_lanes: ['database-migrations'],
          rank: 2,
          rank_inputs: {
            continuously_eligible_age_microseconds: 120_000_000,
            continuously_eligible_since: '2026-08-13T09:58:00Z',
            critical_path_member: false,
            critical_path_position: null,
            priority: 2,
            risk_level: 'critical',
            risk_severity: 3,
            unlock_count: 1,
          },
          reasons: [
            {
              code: 'snapshot_incomplete',
              limit: null,
              observed: null,
              reserved_capacity: null,
              selector: null,
              source_code: 'pagination_gap',
            },
            {
              code: 'review_budget',
              limit: 2,
              observed: 3,
              reserved_capacity: null,
              selector: null,
              source_code: null,
            },
            {
              code: 'changes_requested_reserve',
              limit: 3,
              observed: 2,
              reserved_capacity: 1,
              selector: null,
              source_code: null,
            },
            {
              code: 'component_lane',
              limit: 1,
              observed: 2,
              reserved_capacity: null,
              selector: longComponent,
              source_code: null,
            },
            {
              code: 'protected_lane',
              limit: 1,
              observed: 2,
              owner_ticket_keys: ['ATLAS-250'],
              reserved_capacity: null,
              selector: 'database-migrations',
              source_code: null,
            },
          ],
          ticket_key: 'ATLAS-752',
        },
      ],
      decisions_truncated: false,
      evaluated_at: '2026-08-13T10:02:00Z',
      policy_fingerprint: 'c'.repeat(64),
      policy_revision: revision,
      run_id: '00000000-0000-4000-8000-000000000752',
      selected_ticket_key: 'ATLAS-751',
      snapshot_fingerprint: 'd'.repeat(64),
      snapshot_observed_at: '2026-08-13T10:01:00Z',
    },
    occupancy: {
      changes_requested_occupancy: 1,
      changes_requested_reserve_remaining: 0,
      component_lane_occupancy: [
        { component: longComponent, count: 2, limit: 1 },
      ],
      new_admission_working_capacity: 0,
      over_capacity_reasons: [
        { count: 3, dimension: 'review', limit: 2, selector: null },
        {
          count: 2,
          dimension: 'component_lane',
          limit: 1,
          selector: longComponent,
        },
      ],
      review_occupancy: 3,
      risk_lane_occupancy: [
        { count: 1, limit: 2, risk_level: 'high' },
        { count: 1, limit: 1, risk_level: 'critical' },
      ],
      source: 'materialized_atlas_statuses',
      status_occupancy: [
        { count: 1, status: 'in_progress' },
        { count: 1, status: 'changes_requested' },
        { count: 2, status: 'review_required' },
      ],
      working_occupancy: 2,
    },
    policy: {
      approved_symphony_ceiling: 3,
      changes_requested_reserve: 1,
      component_lane_limits: [{ component: longComponent, limit: 1 }],
      created_at: '2026-08-13T09:00:00Z',
      id: `00000000-0000-4000-8000-${String(revision).padStart(12, '0')}`,
      integration_budget: 2,
      mode,
      review_budget: 2,
      revision,
      risk_lane_limits: [
        { limit: 2, risk_level: 'high' },
        { limit: 1, risk_level: 'critical' },
      ],
      working_budget: 3,
    },
  }
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

function requestHeader(init: RequestInit | undefined, name: string): string | null {
  return new Headers(init?.headers).get(name)
}

function createDeliveryControlTestRouter() {
  const rootRoute = createRootRoute()
  const deliveryControlRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: 'delivery-control',
    component: DeliveryControlView,
  })
  const ticketDetailRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: 'tickets/$key',
    component: () => <p>Ticket detail destination</p>,
  })
  return createRouter({
    routeTree: rootRoute.addChildren([deliveryControlRoute, ticketDetailRoute]),
  })
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

async function renderControl(queryClient: QueryClient = createAtlasQueryClient()) {
  window.history.pushState({}, '', '/delivery-control')
  container = document.createElement('div')
  document.body.append(container)
  mountedRoot = createRoot(container)
  await act(async () => {
    mountedRoot?.render(
      <AppProviders queryClient={queryClient}>
        <RouterProvider router={createDeliveryControlTestRouter()} />
      </AppProviders>
    )
  })
  await waitForAssertion(() =>
    expect(document.body.textContent).toContain('Operator session required')
  )
  return queryClient
}

async function signIn(): Promise<void> {
  await click('Sign in to delivery control')
  const token = document.querySelector<HTMLInputElement>('#atlas-bootstrap-token')
  if (!token) throw new Error('Missing bootstrap token input')
  setInput(token, 'delivery-control-component-token')
  await click('Sign in')
  await waitForAssertion(() =>
    expect(document.body.textContent).toContain('Active Atlas delivery policy')
  )
}

async function openConfirmation(): Promise<void> {
  await click('Review complete replacement')
  await waitForAssertion(() =>
    expect(document.body.textContent).toContain('Confirm complete policy replacement')
  )
}

async function confirmProposal(): Promise<void> {
  const checkbox = document.querySelector<HTMLElement>('[role="checkbox"]')
  if (!checkbox) throw new Error('Missing complete policy confirmation')
  await act(async () => checkbox.click())
  await click('Confirm and submit')
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

describe('delivery control browser component', () => {
  it('routes decision ticket links inside the SPA without losing the operator session', async () => {
    let sessionCreates = 0
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        sessionCreates += 1
        return jsonResponse({
          authenticated: true,
          csrf_token: 'delivery-control-navigation-csrf',
          expires_at: '2099-08-13T11:00:00Z',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      if (path === '/api/v1/delivery-control') {
        return jsonResponse(deliveryControl())
      }
      return new Response('Not found', { status: 404 })
    })

    await renderControl()
    await signIn()

    const ticketLink = Array.from(document.querySelectorAll<HTMLAnchorElement>('a')).find(
      (item) => item.textContent?.trim() === 'ATLAS-751'
    )
    expect(ticketLink?.getAttribute('href')).toBe('/tickets/ATLAS-751')

    await act(async () => ticketLink?.click())
    await waitForAssertion(() =>
      expect(window.location.pathname).toBe('/tickets/ATLAS-751')
    )
    expect(sessionCreates).toBe(1)
  })

  it('renders policy ceiling 3 strictly as Atlas policy state and preserves every server decision and exceptional reason', async () => {
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'delivery-control-csrf',
          expires_at: '2099-08-13T11:00:00Z',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      return jsonResponse(deliveryControl())
    })

    await renderControl()
    await signIn()

    const text = document.body.textContent ?? ''
    expect(text).toContain('Approved policy ceiling')
    expect(text).toContain('Maximum 3')
    expect(text).toContain('Approved policy ceiling is Atlas policy state')
    expect(text).toContain('does not report occupied Symphony workers')
    expect(text).toContain('configured Symphony ceiling is governed separately')
    expect(text).toContain('3 used')
    expect(text).toContain('protected capacity remaining')
    expect(text).toContain(longComponent)
    expect(text).toContain('Server decision: Admit')
    expect(text).toContain('Server decision: Hold')
    expect(text).toContain('snapshot_incomplete')
    expect(text).toContain('pagination_gap')
    expect(text).toContain('review_budget')
    expect(text).toContain('changes_requested_reserve')
    expect(text).toContain('component_lane')
    expect(text).toContain('Protected integration lanes')
    expect(text).toContain('database-migrations')
    expect(text).toContain('protected-integration-lanes/v1')
    expect(text).toContain('current owners ATLAS-250')
    expect(text).toContain('Server reports over-capacity state')
    expect(text).toContain('Server reports indeterminate delivery state')
    expect(text).toContain('write_indeterminate')

    for (const forbiddenLabel of [
      'Live Symphony ceiling',
      'Current Symphony workers',
      'Active workers',
      'Runtime concurrency',
    ]) {
      expect(text).not.toContain(forbiddenLabel)
    }
    for (const forbiddenControl of [
      'Promote ticket',
      'Demote ticket',
      'Dispatch worker',
      'Terminate worker',
      'Cancel worker',
      'Edit WORKFLOW.md',
      'Configure Symphony',
      'Merge pull request',
      'Rebase branch',
      'Optimise policy',
      'Advance ramp',
    ]) {
      expect(
        Array.from(document.querySelectorAll('button')).some((item) =>
          item.textContent?.includes(forbiddenControl)
        )
      ).toBe(false)
    }
  })

  it.each([
    ['paused', 'Paused: no new admission occurs', 'does not terminate'],
    ['draining', 'Draining: no new admission occurs', 'does not stop or terminate'],
  ] as const)('explains %s mode without implying active-session termination', async (mode, copy, terminationCopy) => {
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'mode-csrf',
          expires_at: '2099-08-13T11:00:00Z',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      return jsonResponse(deliveryControl(mode))
    })

    await renderControl()
    await signIn()
    expect(document.body.textContent).toContain(copy)
    expect(document.body.textContent?.toLowerCase()).toContain(
      'already-active work is preserved'
    )
    expect(document.body.textContent).toContain(terminationCopy)
  })

  it('confirms and submits the complete generated policy with a fresh key, then refetches server authority', async () => {
    let current = deliveryControl()
    const submitted: Array<{ body: Schema['DeliveryAdmissionPolicyRequest']; key: string }> = []
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'policy-success-csrf',
          expires_at: '2099-08-13T11:00:00Z',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      if (path === '/api/v1/delivery-control/policy') {
        const body = JSON.parse(String(init?.body)) as Schema['DeliveryAdmissionPolicyRequest']
        submitted.push({
          body,
          key: requestHeader(init, 'Idempotency-Key') ?? '',
        })
        current = deliveryControl(body.mode, current.policy.revision + 1)
        current.policy = {
          ...current.policy,
          approved_symphony_ceiling: body.approved_symphony_ceiling,
          changes_requested_reserve: body.changes_requested_reserve,
          component_lane_limits: body.component_lane_limits,
          integration_budget: body.integration_budget,
          review_budget: body.review_budget,
          risk_lane_limits: body.risk_lane_limits,
          working_budget: body.working_budget,
        }
        return jsonResponse({ policy: current.policy, receipt })
      }
      return jsonResponse(current)
    })

    await renderControl()
    await signIn()
    await openConfirmation()

    const summary = document.querySelector('[aria-label="Complete proposed policy summary"]')
    expect(summary?.textContent).toContain('Running')
    expect(summary?.textContent).toContain('Approved policy ceiling')
    expect(summary?.textContent).toContain('Working budget')
    expect(summary?.textContent).toContain('Integration budget')
    expect(summary?.textContent).toContain('Review budget')
    expect(summary?.textContent).toContain('Changes Requested reserve')
    expect(summary?.textContent).toContain('Risk lane limits')
    expect(summary?.textContent).toContain('Component lane limits')
    expect(summary?.textContent).toContain('Expected policy revision')
    expect(summary?.textContent).toContain('7')
    expect(button('Confirm and submit').disabled).toBe(true)

    await confirmProposal()
    await waitForAssertion(() => {
      expect(document.body.textContent).toContain('authoritative policy revision 8')
      expect(document.body.textContent).toContain(receipt.receipt_id)
    })
    expect(submitted[0].body).toEqual({
      approved_symphony_ceiling: 3,
      changes_requested_reserve: 1,
      component_lane_limits: [{ component: longComponent, limit: 1 }],
      expected_revision: 7,
      integration_budget: 2,
      mode: 'running',
      review_budget: 2,
      risk_lane_limits: [
        { limit: 2, risk_level: 'high' },
        { limit: 1, risk_level: 'critical' },
      ],
      working_budget: 3,
    })
    expect(submitted[0].key).not.toBe('')
    expect(requestHeader((window.fetch as ReturnType<typeof vi.fn>).mock.calls.find(
      ([path]) => requestPath(path) === '/api/v1/delivery-control/policy'
    )?.[1], 'X-Atlas-CSRF')).toBe('policy-success-csrf')

    await openConfirmation()
    await confirmProposal()
    await waitForAssertion(() => expect(submitted).toHaveLength(2))
    expect(submitted[1].body.expected_revision).toBe(8)
    expect(submitted[1].key).not.toBe(submitted[0].key)
  })

  it('records an unambiguous policy success but blocks another command until a failed authoritative refetch recovers', async () => {
    let deliveryReads = 0
    let policyPosts = 0
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'success-refetch-csrf',
          expires_at: '2099-08-13T11:00:00Z',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      if (path === '/api/v1/delivery-control/policy') {
        policyPosts += 1
        return jsonResponse({ policy: deliveryControl('running', 8).policy, receipt })
      }
      deliveryReads += 1
      if (deliveryReads > 1) throw new TypeError('refetch unavailable')
      return jsonResponse(deliveryControl())
    })

    await renderControl()
    await signIn()
    await openConfirmation()
    await confirmProposal()

    await waitForAssertion(() => {
      expect(document.body.textContent).toContain('policy command succeeded')
      expect(document.body.textContent).toContain(receipt.receipt_id)
      expect(document.body.textContent).toContain('Last truthful server snapshot — stale')
    })
    expect(document.body.textContent).not.toContain('Retry exact command safely')
    expect(button('Review complete replacement').matches(':disabled')).toBe(true)
    expect(policyPosts).toBe(1)
  })

  it.each(['stale_revision', 'idempotency_key_reused'] as const)(
    'preserves the proposal and requires explicit current-policy review after %s',
    async (conflictCode) => {
      const current = deliveryControl()
      window.fetch = vi.fn(async (input, init) => {
        const path = requestPath(input)
        if (path === '/api/v1/session' && init?.method === 'POST') {
          return jsonResponse({
            authenticated: true,
            csrf_token: 'conflict-csrf',
            expires_at: '2099-08-13T11:00:00Z',
          })
        }
        if (path === '/api/v1/session') {
          return jsonResponse({ authenticated: false, expires_at: null })
        }
        if (path === '/api/v1/delivery-control/policy') {
          const safe = deliveryControl('paused', 8).policy
          return jsonResponse(
            {
              conflict_code: conflictCode,
              current_policy: safe,
              detail:
                conflictCode === 'stale_revision'
                  ? 'expected policy revision is stale'
                  : 'idempotency key conflicts with an existing command',
              receipt: null,
            },
            409
          )
        }
        return jsonResponse(current)
      })

      await renderControl()
      await signIn()
      const review = document.querySelector<HTMLInputElement>(
        '#policy-reviewBudget'
      )!
      setInput(review, '1')
      await openConfirmation()
      await confirmProposal()

      await waitForAssertion(() => {
        expect(document.body.textContent).toContain('Policy command blocked')
        expect(document.body.textContent).toContain(conflictCode)
        expect(document.body.textContent).toContain('entered proposal is preserved')
      })
      expect(review.value).toBe('1')
      expect(document.body.textContent).toContain('Server current revision 8')
      expect(button('Load and review current policy')).toBeTruthy()
      await click('Load and review current policy')
      expect(review.value).toBe('2')
      expect(
        document.querySelector<HTMLInputElement>('#policy-expected-revision')?.value
      ).toBe('8')
    }
  )

  it('retains a truthful stale snapshot when a refetch is unavailable', async () => {
    let deliveryReads = 0
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'stale-csrf',
          expires_at: '2099-08-13T11:00:00Z',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      deliveryReads += 1
      if (deliveryReads > 1) throw new TypeError('API unavailable')
      return jsonResponse(deliveryControl())
    })

    await renderControl()
    await signIn()
    await click('Refresh server state')
    await waitForAssertion(() => {
      expect(document.body.textContent).toContain('Last truthful server snapshot — stale')
      expect(document.body.textContent).toContain('Policy revision')
      expect(document.body.textContent).toContain('ATLAS-751')
    })
  })

  it('retains one exact command key for an explicit retry after ambiguous API unavailability', async () => {
    const keys: string[] = []
    let postCount = 0
    let current = deliveryControl()
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'ambiguous-csrf',
          expires_at: '2099-08-13T11:00:00Z',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      if (path === '/api/v1/delivery-control/policy') {
        postCount += 1
        keys.push(requestHeader(init, 'Idempotency-Key') ?? '')
        if (postCount === 1) throw new TypeError('connection dropped')
        current = deliveryControl('running', 8)
        return jsonResponse({ policy: current.policy, receipt })
      }
      return jsonResponse(current)
    })

    await renderControl()
    await signIn()
    await openConfirmation()
    await confirmProposal()
    await waitForAssertion(() =>
      expect(document.body.textContent).toContain('Retry exact command safely')
    )
    await click('Retry exact command safely')
    await waitForAssertion(() => expect(keys).toHaveLength(2))
    expect(keys[0]).not.toBe('')
    expect(keys[1]).toBe(keys[0])
  })

  it.each([
    [401, 'operator session expired'],
    [403, 'Security failure'],
  ] as const)('preserves the complete proposal after HTTP %s recovery', async (status, message) => {
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'recovery-csrf',
          expires_at: '2099-08-13T11:00:00Z',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      if (path === '/api/v1/delivery-control/policy') {
        return jsonResponse({ detail: 'governed command refused' }, status)
      }
      return jsonResponse(deliveryControl())
    })

    await renderControl()
    await signIn()
    const review = document.querySelector<HTMLInputElement>('#policy-reviewBudget')!
    setInput(review, '1')
    await openConfirmation()
    await confirmProposal()
    await waitForAssertion(() =>
      expect((document.body.textContent ?? '').toLowerCase()).toContain(message.toLowerCase())
    )
    expect(review.value).toBe('1')
  })
})
