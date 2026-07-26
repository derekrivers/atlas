import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { RouterProvider } from '@tanstack/react-router'
import { afterEach, describe, expect, it } from 'vitest'
import { AppProviders } from '@/app-providers'
import { createOperatorRouter } from '@/router'

let mountedRoot: Root | undefined
let container: HTMLDivElement | undefined

async function renderAt(path: string) {
  window.history.pushState({}, '', path)
  container = document.createElement('div')
  document.body.append(container)
  mountedRoot = createRoot(container)

  await act(async () => {
    mountedRoot?.render(
      <AppProviders>
        <RouterProvider router={createOperatorRouter()} />
      </AppProviders>
    )
  })
}

afterEach(() => {
  mountedRoot?.unmount()
  container?.remove()
  mountedRoot = undefined
  container = undefined
})

describe('operator shell browser rendering', () => {
  it('renders the vendored shell around a placeholder route', async () => {
    await renderAt('/')

    expect(document.body.textContent).toContain('Atlas Operator')
    expect(document.body.textContent).toContain('Operational Snapshot')
    expect(document.body.textContent).toContain('Ticket Board')
    expect(document.body.textContent).toContain('Review Queue')
  })

  it('keeps the not-found route shape', async () => {
    await renderAt('/not-a-route')

    expect(document.body.textContent).toContain('404')
    expect(document.body.textContent).toContain('Page Not Found')
    expect(document.body.textContent).toContain('Back to Home')
  })
})
