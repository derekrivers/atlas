import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { RouterProvider } from '@tanstack/react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppProviders } from '@/app-providers'
import { createAtlasQueryClient } from '@/api/query-policy'
import { createOperatorRouter } from '@/router'
import type { TicketBoardItem } from '@/features/tickets/ticket-board-state'

let mountedRoot: Root | undefined
let container: HTMLDivElement | undefined
let originalFetch: typeof window.fetch

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
    epic_key: 'ATLAS-E1',
    key: 'ATLAS-100',
    priority: 100,
    risk_level: 'critical',
    status: 'rejected',
    ticket_type: 'tech_debt',
    title: 'Seeded ATLAS-100',
  },
] satisfies TicketBoardItem[]

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
})
