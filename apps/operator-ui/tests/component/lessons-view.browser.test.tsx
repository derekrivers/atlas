import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import type { QueryClient } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { components } from '@/api/atlas-openapi'
import { atlasOpenApiEnums } from '@/api/atlas-openapi-runtime'
import { createAtlasQueryClient } from '@/api/query-policy'
import { AppProviders } from '@/app-providers'
import { LessonsView } from '@/features/lessons/lessons-view'

type LessonItem = components['schemas']['LessonItemSchema']
type EntityStatus = components['schemas']['EntityStatus']

function formatEnumValue(value: string): string {
  return value
    .split('_')
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(' ')
}

let mountedRoot: Root | undefined
let container: HTMLDivElement | undefined
let originalFetch: typeof window.fetch

function bodyText(): string {
  return document.body.textContent ?? ''
}

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === 'string') {
    return input
  }

  if (input instanceof URL) {
    return `${input.pathname}${input.search}`
  }

  return input.url
}

function makeLesson(
  status: EntityStatus,
  overrides: Partial<LessonItem> = {}
): LessonItem {
  return {
    category: 'testing',
    confidence: 0.7,
    created_at: '2026-07-26T18:43:42+00:00',
    created_by_id: 'operator-ui-component-seed',
    created_by_type: 'agent',
    id: `00000000-0000-4000-8000-0000000000${status.length}`,
    outcome: `Full ${status} outcome text.`,
    problem: `Full ${status} problem text.`,
    product_id: '00000000-0000-4000-8000-000000000001',
    related_adr_ids: [],
    related_ticket_ids: [`00000000-0000-4000-8000-1000000000${status.length}`],
    solution: `Full ${status} solution text.`,
    source_ticket_id: `00000000-0000-4000-8000-2000000000${status.length}`,
    status,
    tags: ['operator-ui', status],
    title: `${formatEnumValue(status)} lesson`,
    updated_at: '2026-07-26T18:43:42+00:00',
    ...overrides,
  }
}

async function waitForAssertion(assertion: () => void): Promise<void> {
  let lastError: unknown

  for (let attempt = 0; attempt < 60; attempt += 1) {
    await act(async () => {
      await new Promise((resolve) => {
        setTimeout(resolve, 10)
      })
    })

    try {
      assertion()
      return
    } catch (error) {
      lastError = error
    }
  }

  if (lastError instanceof Error) {
    throw lastError
  }
  throw new Error('Timed out waiting for assertion')
}

function statusTab(status: EntityStatus): HTMLButtonElement {
  const label = formatEnumValue(status)
  const tab = Array.from(
    document.querySelectorAll<HTMLButtonElement>('[role="tab"]')
  ).find((button) => button.textContent?.includes(label))

  if (!tab) {
    throw new Error(`Missing ${label} status tab`)
  }

  return tab
}

async function clickStatus(status: EntityStatus): Promise<void> {
  await act(async () => {
    statusTab(status).dispatchEvent(
      new MouseEvent('mousedown', {
        bubbles: true,
        button: 0,
        cancelable: true,
      })
    )
  })
}

async function clickButton(labelPattern: RegExp): Promise<void> {
  const button = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(
    (element) => labelPattern.test(element.getAttribute('aria-label') ?? '')
  )

  if (!button) {
    throw new Error(`Missing button matching ${labelPattern}`)
  }

  await act(async () => {
    button.click()
  })
}

function visibleInteractiveLabels(): string[] {
  return Array.from(
    document.querySelectorAll<HTMLElement>(
      [
        'a',
        'button',
        '[role="button"]',
        '[role="link"]',
        '[role="tab"]',
      ].join(',')
    )
  )
    .filter((element) => {
      const style = window.getComputedStyle(element)
      const box = element.getBoundingClientRect()
      return (
        style.visibility !== 'hidden' &&
        style.display !== 'none' &&
        box.width > 0 &&
        box.height > 0
      )
    })
    .map((element) =>
      [
        element.getAttribute('aria-label'),
        element.textContent,
        element.getAttribute('title'),
      ]
        .filter(Boolean)
        .join(' ')
        .replace(/\s+/g, ' ')
        .trim()
    )
    .filter(Boolean)
}

async function render(
  component: ReactNode,
  queryClient: QueryClient
): Promise<void> {
  container = document.createElement('div')
  document.body.append(container)
  mountedRoot = createRoot(container)

  await act(async () => {
    mountedRoot?.render(
      <AppProviders queryClient={queryClient}>{component}</AppProviders>
    )
  })
}

async function renderLessons(lessons: readonly LessonItem[]): Promise<string[]> {
  const requests: string[] = []
  window.fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    requests.push(requestPath(input))
    return new Response(JSON.stringify({ lessons }), {
      headers: { 'content-type': 'application/json' },
      status: 200,
    })
  })

  await render(<LessonsView />, createAtlasQueryClient())
  await waitForAssertion(() => {
    expect(bodyText()).toContain('Lessons')
  })
  return requests
}

afterEach(() => {
  mountedRoot?.unmount()
  container?.remove()
  window.fetch = originalFetch
  mountedRoot = undefined
  container = undefined
})

describe('lessons view browser rendering', () => {
  it('defaults to draft and reaches every EntityStatus facet client-side', async () => {
    originalFetch = window.fetch
    const lessons = atlasOpenApiEnums.EntityStatus.map((status) =>
      makeLesson(status)
    )
    const requests = await renderLessons(lessons)

    await waitForAssertion(() => {
      expect(statusTab('draft').getAttribute('aria-selected')).toBe('true')
      expect(
        document.querySelector('[role="tablist"][aria-label="Lesson status facets"]')
      ).not.toBeNull()
      expect(document.querySelector('table[aria-label="Lessons"]')).not.toBeNull()
      expect(bodyText()).toContain('Draft lesson')
      for (const status of atlasOpenApiEnums.EntityStatus) {
        if (status !== 'draft') {
          expect(bodyText()).not.toContain(`${formatEnumValue(status)} lesson`)
        }
      }
    })

    for (const status of atlasOpenApiEnums.EntityStatus) {
      await clickStatus(status)
      await waitForAssertion(() => {
        expect(statusTab(status).getAttribute('aria-selected')).toBe('true')
        expect(bodyText()).toContain(`${formatEnumValue(status)} lesson`)
      })
    }

    expect(requests.filter((request) => request.startsWith('/api/v1/lessons'))).toEqual([
      '/api/v1/lessons',
    ])
  })

  it('opens a read-only drawer with full lesson detail and literal UUIDs', async () => {
    originalFetch = window.fetch
    const sourceTicketId = '00000000-0000-4000-8000-00000000f001'
    const relatedTicketId = '00000000-0000-4000-8000-00000000f002'
    await renderLessons([
      makeLesson('draft', {
        outcome: 'The operator sees the entire outcome without truncation.',
        problem:
          'The problem text is intentionally longer than a summary and must render in full.',
        related_ticket_ids: [relatedTicketId],
        solution:
          'The solution text is also full length and remains available in the drawer.',
        source_ticket_id: sourceTicketId,
        title: 'Literal UUID lesson',
      }),
    ])

    await clickButton(/View lesson details: Literal UUID lesson/)

    await waitForAssertion(() => {
      expect(bodyText()).toContain(
        'The problem text is intentionally longer than a summary and must render in full.'
      )
      expect(bodyText()).toContain(
        'The solution text is also full length and remains available in the drawer.'
      )
      expect(bodyText()).toContain(
        'The operator sees the entire outcome without truncation.'
      )
      expect(bodyText()).toContain(sourceTicketId)
      expect(bodyText()).toContain(relatedTicketId)
    })

    const interactiveLabels = visibleInteractiveLabels()
    expect(
      interactiveLabels.filter(
        (label) => label.includes(sourceTicketId) || label.includes(relatedTicketId)
      )
    ).toEqual([])
  })

  it('exposes disposition only for DRAFT and no edit, merge, or ACTIVE archive affordance', async () => {
    originalFetch = window.fetch
    const lessons = atlasOpenApiEnums.EntityStatus.map((status) =>
      makeLesson(status)
    )
    await renderLessons(lessons)
    const observedLabels: string[] = []

    for (const status of atlasOpenApiEnums.EntityStatus) {
      await clickStatus(status)
      await waitForAssertion(() => {
        expect(bodyText()).toContain(`${formatEnumValue(status)} lesson`)
      })
      observedLabels.push(...visibleInteractiveLabels())
    }

    await clickStatus('draft')
    await clickButton(/View lesson details: Draft lesson/)
    await waitForAssertion(() => {
      expect(bodyText()).toContain('Promote')
      expect(bodyText()).toContain('Reject')
    })

    const closeButton = Array.from(
      document.querySelectorAll<HTMLButtonElement>('button')
    ).find((button) => button.textContent?.includes('Close'))
    await act(async () => closeButton?.click())

    await clickStatus('deprecated')
    await clickButton(/View lesson details: Deprecated lesson/)
    await waitForAssertion(() => {
      expect(bodyText()).toContain('Full deprecated outcome text.')
    })
    observedLabels.push(...visibleInteractiveLabels())

    const offenders = observedLabels.filter((label) =>
      /\b(edit|merge|archive|rebase|pull request)\b/i.test(label)
    )
    expect(offenders).toEqual([])
    expect(visibleInteractiveLabels().filter((label) => /\b(promote|reject)\b/i.test(label))).toEqual([])
  })

  it('renders the shared empty state when the lesson collection is empty', async () => {
    originalFetch = window.fetch
    await renderLessons([])

    await waitForAssertion(() => {
      expect(document.querySelector('[role="status"]')).not.toBeNull()
      expect(bodyText()).toContain('No lessons')
      expect(bodyText()).toContain(
        'The Atlas API returned an empty lesson collection.'
      )
    })
  })
})
