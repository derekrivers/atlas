import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { RouterProvider } from '@tanstack/react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppProviders } from '@/app-providers'
import { createAtlasQueryClient } from '@/api/query-policy'
import { createOperatorRouter } from '@/router'
import type {
  EpicItem,
  TicketBoardItem,
} from '@/features/tickets/ticket-board-state'

let mountedRoot: Root | undefined
let container: HTMLDivElement | undefined
let originalFetch: typeof window.fetch

const tickets = [
  {
    epic_key: 'ATLAS-E2',
    key: 'ATLAS-1',
    priority: 1,
    risk_level: 'low',
    status: 'done',
    ticket_type: 'feature',
    title: 'Seeded ATLAS-1',
  },
  {
    epic_key: 'ATLAS-E1',
    key: 'ATLAS-10',
    priority: 10,
    risk_level: 'medium',
    status: 'planned',
    ticket_type: 'feature',
    title: 'Seeded ATLAS-10',
  },
  {
    epic_key: 'ATLAS-E1',
    key: 'ATLAS-2',
    priority: 2,
    risk_level: 'high',
    status: 'in_progress',
    ticket_type: 'bug',
    title: 'Seeded ATLAS-2',
  },
  {
    epic_key: null,
    key: 'ATLAS-100',
    priority: 100,
    risk_level: 'critical',
    status: 'rejected',
    ticket_type: 'tech_debt',
    title: 'Seeded ATLAS-100',
  },
] satisfies TicketBoardItem[]

const epics = [
  {
    completed_at: null,
    created_at: '2026-07-26T18:43:42Z',
    created_by_id: 'operator-ui-e2e-seed',
    created_by_type: 'system',
    description: 'Seeded operator-ui e2e epic.',
    id: '00000000-0000-4000-8000-000000000e01',
    key: 'ATLAS-E1',
    objective: 'Host active operator UI tickets.',
    priority: 1,
    product_id: '00000000-0000-4000-8000-000000000388',
    risk_level: 'medium',
    source_anchor: 'docs/atlas/operator-ui.md#testing-contract',
    status: 'planned',
    title: 'Operator UI e2e seed',
    updated_at: '2026-07-26T18:43:42Z',
  },
  {
    completed_at: null,
    created_at: '2026-07-26T18:43:42Z',
    created_by_id: 'operator-ui-e2e-seed',
    created_by_type: 'system',
    description: 'Second seeded operator-ui e2e epic.',
    id: '00000000-0000-4000-8000-000000000e02',
    key: 'ATLAS-E2',
    objective: 'Host terminal operator UI tickets.',
    priority: 2,
    product_id: '00000000-0000-4000-8000-000000000388',
    risk_level: 'low',
    source_anchor: 'docs/atlas/operator-ui.md#testing-contract',
    status: 'archived',
    title: 'Terminal archive seed',
    updated_at: '2026-07-26T18:43:42Z',
  },
] satisfies EpicItem[]

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

function rowKeys(): string[] {
  return Array.from(document.querySelectorAll('[data-testid="ticket-board-row"]'))
    .map((row) => row.querySelector('td')?.textContent?.trim() ?? '')
    .filter(Boolean)
}

function groupKeys(epicKey: string): string[] {
  const group = document.querySelector(
    `[data-testid="ticket-board-epic-group"][data-epic-key="${epicKey}"]`
  )
  if (!group) {
    throw new Error(`Epic group ${epicKey} not found`)
  }
  return Array.from(group.querySelectorAll('[data-testid="ticket-board-row"]'))
    .map((row) => row.querySelector('td')?.textContent?.trim() ?? '')
    .filter(Boolean)
}

function groupCounts(): number[] {
  return Array.from(
    document.querySelectorAll('[data-testid="ticket-board-epic-group-count"]')
  ).map((count) => {
    const value = count.textContent?.match(/\d+/)?.[0]
    if (!value) {
      throw new Error('Group count did not contain a number')
    }
    return Number(value)
  })
}

function buttonByName(pattern: RegExp): HTMLButtonElement {
  const button = Array.from(document.querySelectorAll('button')).find((item) =>
    pattern.test(item.textContent ?? '')
  )
  if (!button) {
    throw new Error(`Button ${pattern} not found`)
  }
  return button
}

beforeEach(() => {
  originalFetch = window.fetch
  window.fetch = vi.fn(async (input) => {
    const path = requestPath(input)
    if (path === '/api/v1/status') {
      return jsonResponse({
        evidence_count: 0,
        last_evidence_pull_at: null,
        last_linear_sync_at: null,
        package_version: '0.0.0',
        schema_revision: null,
        ticket_count: tickets.length,
      })
    }
    if (path === '/api/v1/tickets') {
      return jsonResponse({ tickets })
    }
    if (path === '/api/v1/epics') {
      return jsonResponse({ epics })
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

describe('ticket board browser rendering', () => {
  it('hides terminal rows by default and sorts ticket keys naturally', async () => {
    await renderAt('/tickets')

    await waitFor(() => {
      expect(rowKeys()).toEqual(['ATLAS-2', 'ATLAS-10'])
    })

    const firstRow = document.querySelector('[data-testid="ticket-board-row"]')
    expect(firstRow?.textContent).toContain('Seeded ATLAS-2')
    expect(firstRow?.textContent).toContain('In Progress')
    expect(firstRow?.querySelector('a')?.getAttribute('href')).toBe(
      '/tickets/ATLAS-2'
    )
  })

  it('reveals terminal rows in one interaction and keeps natural key order', async () => {
    await renderAt('/tickets')
    await waitFor(() => expect(rowKeys()).toEqual(['ATLAS-2', 'ATLAS-10']))

    await act(async () => {
      buttonByName(/Show terminal/).click()
    })

    await waitFor(() => {
      expect(rowKeys()).toEqual(['ATLAS-1', 'ATLAS-2', 'ATLAS-10', 'ATLAS-100'])
    })
    expect(window.location.search).toContain('terminal=show')
  })

  it('hydrates filter and sort state from the URL', async () => {
    await renderAt('/tickets?q=ATLAS-10&terminal=show&sort=key.desc')

    await waitFor(() => {
      expect(rowKeys()).toEqual(['ATLAS-100', 'ATLAS-10'])
    })
    expect(
      document.querySelector<HTMLInputElement>('input[aria-label="Search tickets"]')
        ?.value
    ).toBe('ATLAS-10')
  })

  it('groups visible rows by epic without resetting terminal defaults or natural key sort', async () => {
    await renderAt('/tickets?mode=epic')

    await waitFor(() => {
      expect(groupKeys('ATLAS-E1')).toEqual(['ATLAS-2', 'ATLAS-10'])
    })
    expect(document.body.textContent).toContain('Operator UI e2e seed')
    expect(document.body.textContent).not.toContain('ATLAS-100')
  })

  it('keeps unassigned tickets in an explicit group whose counts sum to the filtered rows', async () => {
    await renderAt('/tickets?mode=epic&terminal=show')

    await waitFor(() => {
      expect(groupKeys('unassigned')).toEqual(['ATLAS-100'])
    })

    const counts = groupCounts()
    expect(counts.reduce((sum, count) => sum + count, 0)).toBe(rowKeys().length)
    expect(
      document.querySelector('[data-epic-key="unassigned"]')?.textContent
    ).toContain('Tickets without an epic')
  })

  it('filters grouped rows by epic from the URL', async () => {
    await renderAt('/tickets?mode=epic&terminal=show&epic=unassigned')

    await waitFor(() => {
      expect(rowKeys()).toEqual(['ATLAS-100'])
    })
    expect(
      document.querySelectorAll('[data-testid="ticket-board-epic-group"]')
    ).toHaveLength(1)
  })
})
