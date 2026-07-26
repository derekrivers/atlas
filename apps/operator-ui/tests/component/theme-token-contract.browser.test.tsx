import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import '@/styles/index.css'

const divergentTheme = `
:root {
  --radius: 1.25rem;
  --background: rgb(252 249 240);
  --foreground: rgb(22 28 38);
  --card: rgb(240 251 247);
  --card-foreground: rgb(19 36 30);
  --primary: rgb(86 41 183);
  --primary-foreground: rgb(255 252 241);
  --border: rgb(18 126 112);
}
`

let mountedRoot: Root | undefined
let container: HTMLDivElement | undefined
let tokenSwap: HTMLStyleElement | undefined

function PrimitiveProbe() {
  return (
    <div>
      <Button>Token action</Button>
      <Badge>Token badge</Badge>
      <div
        data-token-probe='card'
        className='bg-card text-card-foreground border-border rounded-lg border p-4'
      >
        Token surface
      </div>
    </div>
  )
}

async function renderProbe() {
  container = document.createElement('div')
  document.body.append(container)
  mountedRoot = createRoot(container)

  await act(async () => {
    mountedRoot?.render(<PrimitiveProbe />)
  })
}

async function swapTokenFile() {
  tokenSwap = document.createElement('style')
  tokenSwap.textContent = divergentTheme
  document.head.append(tokenSwap)
  await new Promise(requestAnimationFrame)
}

function styleFor(selector: string) {
  const element = document.querySelector(selector)
  expect(element, selector).toBeInstanceOf(HTMLElement)
  return getComputedStyle(element as HTMLElement)
}

afterEach(() => {
  mountedRoot?.unmount()
  container?.remove()
  tokenSwap?.remove()
  mountedRoot = undefined
  container = undefined
  tokenSwap = undefined
})

describe('vendored theme token rendering contract', () => {
  it('repaints primitive colours and radius when the token file is swapped', async () => {
    await renderProbe()

    const initialButton = styleFor('[data-slot="button"]')
    const initialCard = styleFor('[data-token-probe="card"]')
    const initialButtonBackground = initialButton.backgroundColor
    const initialButtonRadius = initialButton.borderRadius
    const initialCardBackground = initialCard.backgroundColor
    const initialCardBorder = initialCard.borderTopColor

    await swapTokenFile()

    const swappedButton = styleFor('[data-slot="button"]')
    const swappedCard = styleFor('[data-token-probe="card"]')

    expect(swappedButton.backgroundColor).not.toBe(initialButtonBackground)
    expect(swappedButton.borderRadius).not.toBe(initialButtonRadius)
    expect(swappedCard.backgroundColor).not.toBe(initialCardBackground)
    expect(swappedCard.borderTopColor).not.toBe(initialCardBorder)
  })
})
