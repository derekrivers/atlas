import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { RouterProvider } from '@tanstack/react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppProviders } from '@/app-providers'
import { createAtlasQueryClient } from '@/api/query-policy'
import { createOperatorRouter } from '@/router'
import type { CriticalPathResponse } from '@/features/critical-path/selectors'

const statusPayload = {
  evidence_count: 0,
  last_evidence_pull_at: null,
  last_linear_sync_at: null,
  package_version: '0.0.0',
  schema_revision: null,
  ticket_count: 0,
}

let mountedRoot: Root | undefined
let container: HTMLDivElement | undefined
let originalFetch: typeof window.fetch

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    headers: { 'content-type': 'application/json' },
    status: 200,
  })
}

function pathForFetchInput(input: Parameters<typeof fetch>[0]): string {
  if (typeof input === 'string') {
    return new URL(input, window.location.origin).pathname
  }
  if (input instanceof URL) {
    return input.pathname
  }
  return new URL(input.url).pathname
}

async function waitForBodyText(text: string) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    await act(async () => {
      await new Promise((resolve) => {
        setTimeout(resolve, 20)
      })
    })

    if (document.body.textContent?.includes(text)) {
      return
    }
  }

  expect(document.body.textContent).toContain(text)
}

async function render(component: ReactNode) {
  container = document.createElement('div')
  document.body.append(container)
  mountedRoot = createRoot(container)

  await act(async () => {
    mountedRoot?.render(component)
  })
}

async function renderCriticalPath(payload: CriticalPathResponse) {
  window.history.pushState({}, '', '/critical-path')
  window.fetch = vi.fn(async (input) => {
    const path = pathForFetchInput(input)
    if (path === '/api/v1/status') {
      return jsonResponse(statusPayload)
    }
    if (path === '/api/v1/dependencies/critical-path') {
      return jsonResponse(payload)
    }
    return new Response('Not found', { status: 404 })
  })

  await render(
    <AppProviders queryClient={createAtlasQueryClient()}>
      <RouterProvider router={createOperatorRouter()} />
    </AppProviders>
  )
}

function textInRows(testId: string): string[] {
  return Array.from(document.querySelectorAll(`[data-testid="${testId}"]`)).map(
    (element) => element.textContent?.trim() ?? ''
  )
}

beforeEach(() => {
  originalFetch = window.fetch
})

afterEach(() => {
  mountedRoot?.unmount()
  container?.remove()
  window.fetch = originalFetch
  mountedRoot = undefined
  container = undefined
})

describe('critical path browser rendering', () => {
  it('renders the API execution order and effort values without recomputing them', async () => {
    const payload: CriticalPathResponse = {
      keys: ['ATLAS-20', 'ATLAS-3', 'ATLAS-11'],
      steps: [
        { key: 'ATLAS-20', effort: 8, cumulative_effort: 50 },
        { key: 'ATLAS-3', effort: 1, cumulative_effort: 4 },
        { key: 'ATLAS-11', effort: 5, cumulative_effort: 99 },
      ],
      total_effort: 123,
    }

    await renderCriticalPath(payload)
    await waitForBodyText('ATLAS-20')

    expect(
      document.querySelector('table[aria-label="Critical path execution chain"]')
    ).not.toBeNull()
    expect(
      textInRows('critical-path-step-link').map((value) =>
        value.replace(/\s+/g, '')
      )
    ).toEqual(['ATLAS-20', 'ATLAS-3', 'ATLAS-11'])
    expect(textInRows('critical-path-step-effort')).toEqual(['8', '1', '5'])
    expect(textInRows('critical-path-step-cumulative')).toEqual([
      '50',
      '4',
      '99',
    ])
    expect(
      document.querySelector('[data-testid="critical-path-total"]')?.textContent
    ).toBe('123')
  })

  it('states that the critical path is advisory and does not gate dispatch', async () => {
    await renderCriticalPath({
      keys: ['ATLAS-2'],
      steps: [{ key: 'ATLAS-2', effort: 3, cumulative_effort: 3 }],
      total_effort: 3,
    })
    await waitForBodyText('ADVISORY')

    expect(document.body.textContent).toContain('ADVISORY')
    expect(document.body.textContent).toContain(
      'The critical path does not gate dispatch.'
    )
  })

  it('links each step to the ticket detail route', async () => {
    await renderCriticalPath({
      keys: ['ATLAS-7', 'ATLAS-8'],
      steps: [
        { key: 'ATLAS-7', effort: 2, cumulative_effort: 2 },
        { key: 'ATLAS-8', effort: 4, cumulative_effort: 6 },
      ],
      total_effort: 6,
    })
    await waitForBodyText('ATLAS-7')

    const links = Array.from(
      document.querySelectorAll<HTMLAnchorElement>(
        '[data-testid="critical-path-step-link"]'
      )
    )

    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/tickets/ATLAS-7',
      '/tickets/ATLAS-8',
    ])
  })

  it('renders the shared empty state for an empty path', async () => {
    await renderCriticalPath({ keys: [], steps: [], total_effort: 0 })
    await waitForBodyText('No critical path')

    expect(document.querySelector('[role="status"]')?.textContent).toContain(
      'No critical path'
    )
    expect(document.body.textContent).toContain(
      'No non-terminal tickets remain in the dependency graph.'
    )
    expect(document.querySelectorAll('[data-testid="critical-path-step"]')).toHaveLength(
      0
    )
  })
})
