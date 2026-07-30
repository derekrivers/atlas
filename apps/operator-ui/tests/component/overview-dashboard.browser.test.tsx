import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { RouterProvider } from '@tanstack/react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppProviders } from '@/app-providers'
import { createAtlasQueryClient } from '@/api/query-policy'
import { createOperatorRouter } from '@/router'
import { StalenessIndicator } from '@/components/staleness-indicator'
import type { CriticalPathResponse } from '@/features/critical-path/selectors'
import type { TicketBoardItem } from '@/features/tickets/ticket-board-state'
import type { ReviewQueueResponse } from '@/features/reviews/selectors'

const tickets = [
  {
    epic_key: 'ATLAS-E1',
    key: 'ATLAS-1',
    priority: 1,
    risk_level: 'low',
    status: 'done',
    ticket_type: 'feature',
    title: 'Seeded ATLAS-1',
  },
  {
    epic_key: 'ATLAS-E1',
    key: 'ATLAS-2',
    priority: 2,
    risk_level: 'high',
    status: 'in_progress',
    ticket_type: 'feature',
    title: 'Seeded ATLAS-2',
  },
  {
    epic_key: 'ATLAS-E1',
    key: 'ATLAS-3',
    priority: 3,
    risk_level: 'medium',
    status: 'in_progress',
    ticket_type: 'bug',
    title: 'Seeded ATLAS-3',
  },
  {
    epic_key: 'ATLAS-E1',
    key: 'ATLAS-4',
    priority: 4,
    risk_level: 'medium',
    status: 'rejected',
    ticket_type: 'tech_debt',
    title: 'Seeded ATLAS-4',
  },
] satisfies TicketBoardItem[]

const reviews = {
  reviews: [
    {
      checks: [],
      has_pr_merged_evidence: false,
      has_system_evidence: true,
      key: 'ATLAS-2',
      status: 'in_progress',
      ticket_type: 'feature',
      title: 'Seeded ATLAS-2',
      verdict: 'pending',
    },
    {
      checks: [],
      has_pr_merged_evidence: false,
      has_system_evidence: false,
      key: 'ATLAS-3',
      status: 'in_progress',
      ticket_type: 'bug',
      title: 'Seeded ATLAS-3',
      verdict: 'pending',
    },
  ],
} satisfies ReviewQueueResponse

const criticalPath = {
  keys: ['ATLAS-2', 'ATLAS-3'],
  steps: [
    { cumulative_effort: 8, effort: 8, key: 'ATLAS-2' },
    { cumulative_effort: 13, effort: 5, key: 'ATLAS-3' },
  ],
  total_effort: 13,
} satisfies CriticalPathResponse

let mountedRoot: Root | undefined
let container: HTMLDivElement | undefined
let originalFetch: typeof window.fetch

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'content-type': 'application/json' },
    status: 200,
  })
}

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === 'string') {
    return new URL(input, window.location.origin).pathname
  }
  if (input instanceof URL) {
    return input.pathname
  }
  return new URL(input.url, window.location.origin).pathname
}

async function renderAt(path: string) {
  window.history.pushState({}, '', path)
  container = document.createElement('div')
  document.body.append(container)
  mountedRoot = createRoot(container)

  await act(async () => {
    mountedRoot?.render(
      <AppProviders queryClient={createAtlasQueryClient()}>
        <RouterProvider router={createOperatorRouter()} />
      </AppProviders>
    )
  })
}

async function renderComponent(component: React.ReactNode) {
  container = document.createElement('div')
  document.body.append(container)
  mountedRoot = createRoot(container)

  await act(async () => {
    mountedRoot?.render(component)
  })
}

async function waitFor(assertion: () => void) {
  const startedAt = Date.now()
  let lastError: unknown

  while (Date.now() - startedAt < 3_000) {
    try {
      assertion()
      return
    } catch (error) {
      lastError = error
      await new Promise((resolve) => setTimeout(resolve, 25))
    }
  }

  throw lastError
}

function textForTestId(testId: string): string {
  const element = document.querySelector(`[data-testid="${testId}"]`)
  if (!element) {
    throw new Error(`Missing ${testId}`)
  }
  return element.textContent?.trim() ?? ''
}

beforeEach(() => {
  originalFetch = window.fetch
  window.fetch = vi.fn(async (input) => {
    const path = requestPath(input)
    if (path === '/api/v1/status') {
      return jsonResponse({
        evidence_count: 2,
        last_evidence_pull_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
        last_linear_sync_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
        package_version: '0.0.0-test',
        schema_revision: '0021',
        ticket_count: tickets.length,
      })
    }
    if (path === '/api/v1/tickets') {
      return jsonResponse({ tickets })
    }
    if (path === '/api/v1/reviews') {
      return jsonResponse(reviews)
    }
    if (path === '/api/v1/dependencies/critical-path') {
      return jsonResponse(criticalPath)
    }
    return new Response('Not found', { status: 404 })
  })
})

afterEach(() => {
  mountedRoot?.unmount()
  container?.remove()
  window.fetch = originalFetch
  mountedRoot = undefined
  container = undefined
})

describe('overview dashboard browser rendering', () => {
  it('renders API counts, status distribution, review depth, and critical path effort', async () => {
    await renderAt('/')

    await waitFor(() => {
      expect(textForTestId('overview-ticket-count')).toBe('4')
    })

    expect(textForTestId('overview-evidence-count')).toBe('2')
    expect(textForTestId('overview-review-depth')).toBe('2')
    expect(textForTestId('overview-critical-path-total-effort')).toBe('13')
    expect(textForTestId('overview-status-distribution-total')).toBe('4 total')

    const buckets = Array.from(
      document.querySelectorAll('[data-testid="overview-status-bucket"]')
    ).map((bucket) => ({
      count: bucket.querySelector('[data-testid="overview-status-bucket-count"]')
        ?.textContent,
      status: (bucket as HTMLElement).dataset.status,
    }))

    expect(buckets).toEqual([
      { count: '1', status: 'done' },
      { count: '2', status: 'in_progress' },
      { count: '1', status: 'rejected' },
    ])
    expect(document.querySelector('[role="alert"]')).toBeNull()
    expect(
      document
        .querySelector('[data-testid="overview-critical-path-head-link"]')
        ?.getAttribute('href')
    ).toBe('/tickets/ATLAS-2')
  })

  it('renders fresh and stale timestamps as relative staleness with a visible threshold', async () => {
    const now = Date.parse('2026-07-29T12:00:00Z')

    await renderComponent(
      <div>
        <StalenessIndicator
          label='Linear sync'
          now={now}
          testId='fresh-staleness'
          value='2026-07-29T11:55:00Z'
        />
        <StalenessIndicator
          label='Evidence pull'
          now={now}
          testId='stale-staleness'
          value='2026-07-29T10:00:00Z'
        />
      </div>
    )

    const fresh = document.querySelector<HTMLElement>(
      '[data-testid="fresh-staleness"]'
    )
    const stale = document.querySelector<HTMLElement>(
      '[data-testid="stale-staleness"]'
    )

    expect(fresh?.dataset.stalenessState).toBe('fresh')
    expect(fresh?.textContent).toContain('Fresh')
    expect(fresh?.textContent).toContain('5m ago')
    expect(fresh?.textContent).toContain('threshold 30m')
    expect(stale?.dataset.stalenessState).toBe('stale')
    expect(stale?.textContent).toContain('Stale')
    expect(stale?.textContent).toContain('2h ago')
    expect(stale?.textContent).toContain('threshold 30m')
  })

  it('does not render a time-series element', async () => {
    await renderAt('/')

    await waitFor(() => {
      expect(textForTestId('overview-ticket-count')).toBe('4')
    })

    expect(
      document.querySelector(
        '[data-chart-kind="time-series"], [data-testid*="time-series"], canvas'
      )
    ).toBeNull()
    expect(document.body.textContent?.toLowerCase()).not.toContain('trend')
    expect(document.body.textContent?.toLowerCase()).not.toContain('time series')
  })
})
