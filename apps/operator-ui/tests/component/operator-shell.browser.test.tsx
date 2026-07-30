import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { RouterProvider } from '@tanstack/react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppProviders } from '@/app-providers'
import { createAtlasQueryClient } from '@/api/query-policy'
import { createOperatorRouter } from '@/router'

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

async function waitForAssertion(assertion: () => void): Promise<void> {
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

async function renderAt(
  path: string,
  options?: Parameters<typeof createOperatorRouter>[0]
) {
  window.history.pushState({}, '', path)
  container = document.createElement('div')
  document.body.append(container)
  mountedRoot = createRoot(container)

  await act(async () => {
    mountedRoot?.render(
      <AppProviders queryClient={createAtlasQueryClient()}>
        <RouterProvider router={createOperatorRouter(options)} />
      </AppProviders>
    )
  })
}

beforeEach(() => {
  originalFetch = window.fetch
  window.fetch = vi.fn(async (input) => {
    const path = requestPath(input)
    if (path === '/api/v1/lessons') {
      return jsonResponse({ lessons: [] })
    }
    if (path === '/api/v1/status') {
      return jsonResponse({
        evidence_count: 0,
        last_evidence_pull_at: null,
        last_linear_sync_at: null,
        package_version: '0.0.0',
        schema_revision: null,
        ticket_count: 0,
      })
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

describe('operator shell browser rendering', () => {
  it('renders shell controls around a content route', async () => {
    await renderAt('/lessons')

    await waitForAssertion(() => {
      expect(document.body.textContent).toContain('Toggle Sidebar')
      expect(document.body.textContent).toContain('Search routes')
      expect(document.body.textContent).toContain('Lessons')
      expect(document.body.textContent).toContain('No lessons')
    })
  })

  it('keeps the not-found route inside the shell', async () => {
    await renderAt('/not-a-route')

    expect(document.body.textContent).toContain('Toggle Sidebar')
    expect(document.body.textContent).toContain('Search routes')
    expect(document.body.textContent).toContain('404')
    expect(document.body.textContent).toContain('Page Not Found')
    expect(document.body.textContent).toContain('Back to Home')
  })

  it('contains a throwing route with the route-level error boundary', async () => {
    await renderAt('/__atlas-error-probe', { includeErrorProbe: true })

    expect(document.body.textContent).toContain('Toggle Sidebar')
    expect(document.body.textContent).toContain('Search routes')
    expect(document.body.textContent).toContain('Something went wrong')
  })
})
