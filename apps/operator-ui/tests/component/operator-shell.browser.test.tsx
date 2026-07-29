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
  window.fetch = vi.fn(async () => {
    return new Response(
      JSON.stringify({
        evidence_count: 0,
        last_evidence_pull_at: null,
        last_linear_sync_at: null,
        package_version: '0.0.0',
        schema_revision: null,
        ticket_count: 0,
      }),
      {
        headers: { 'content-type': 'application/json' },
        status: 200,
      }
    )
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
  it('renders shell controls around a placeholder route', async () => {
    await renderAt('/')

    expect(document.body.textContent).toContain('Toggle Sidebar')
    expect(document.body.textContent).toContain('Search routes')
    expect(document.body.textContent).toContain('Operational Snapshot')
    expect(document.body.textContent).toContain('Placeholder')
    expect(document.querySelector('[data-sidebar="sidebar"]')).not.toBeNull()
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
    expect(document.querySelector('[data-sidebar="sidebar"]')).not.toBeNull()
  })
})
