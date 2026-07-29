import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import {
  ApiUnreachableState,
  EmptyCollectionState,
  LoadingState,
  RequestErrorState,
} from '@/components/states'

let mountedRoot: Root | undefined
let container: HTMLDivElement | undefined

async function render(component: ReactNode) {
  container = document.createElement('div')
  document.body.append(container)
  mountedRoot = createRoot(container)

  await act(async () => {
    mountedRoot?.render(component)
  })
}

afterEach(() => {
  mountedRoot?.unmount()
  container?.remove()
  mountedRoot = undefined
  container = undefined
})

describe('shared query state primitives', () => {
  it('renders the loading state as an announced status', async () => {
    await render(<LoadingState label='Loading tickets' />)

    expect(document.body.textContent).toContain('Loading tickets')
    expect(document.querySelector('[role="status"]')).not.toBeNull()
  })

  it('renders the empty collection state with caller text', async () => {
    await render(
      <EmptyCollectionState title='No tickets' detail='No records matched.' />
    )

    expect(document.body.textContent).toContain('No tickets')
    expect(document.body.textContent).toContain('No records matched.')
  })

  it('renders request errors without a retry action', async () => {
    await render(<RequestErrorState error={new Error('Request exploded')} />)

    expect(document.body.textContent).toContain('Request failed')
    expect(document.body.textContent).toContain('Request exploded')
    expect(document.body.textContent).not.toContain('Retry')
  })

  it('renders API unreachable as a named actionable state', async () => {
    await render(<ApiUnreachableState apiBaseUrl='http://127.0.0.1:18000' />)

    expect(document.body.textContent).toContain('API unreachable')
    expect(document.body.textContent).toContain('http://127.0.0.1:18000')
    expect(document.body.textContent).toContain('atlas api serve')
  })
})
