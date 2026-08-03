import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import type { QueryClient } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { components } from '@/api/atlas-openapi'
import { atlasForgetSession } from '@/api/client'
import { createAtlasQueryClient } from '@/api/query-policy'
import { AppProviders } from '@/app-providers'
import { LessonsView } from '@/features/lessons/lessons-view'

type LessonItem = components['schemas']['LessonItemSchema']
type Receipt = components['schemas']['OperatorActionReceiptSchema']

const draftLesson: LessonItem = {
  category: 'testing',
  confidence: null,
  created_at: '2026-08-03T10:00:00+00:00',
  created_by_id: 'lesson-disposition-component-test',
  created_by_type: 'agent',
  id: '00000000-0000-4000-8000-000000000408',
  outcome: 'The DRAFT is ready for an operator ruling.',
  problem: 'A lesson needs a human gate.',
  product_id: '00000000-0000-4000-8000-000000000001',
  related_adr_ids: [],
  related_ticket_ids: [],
  solution: 'Use the governed disposition service.',
  source_ticket_id: '00000000-0000-4000-8000-000000000235',
  status: 'draft',
  tags: ['operator-ui'],
  title: 'Governed disposition lesson',
  updated_at: '2026-08-03T10:00:00+00:00',
}

const activeLesson: LessonItem = {
  ...draftLesson,
  confidence: 0.8,
  status: 'active',
  updated_at: '2026-08-03T10:01:00+00:00',
}

const receipt: Receipt = {
  action: 'lesson.promote',
  actor: { id: 'operator', type: 'human' },
  after_status: 'active',
  before_status: 'draft',
  completed_at: '2026-08-03T10:01:00+00:00',
  correlation_id: '00000000-0000-4000-8000-00000000c408',
  created_at: '2026-08-03T10:01:00+00:00',
  idempotency_key_identity: 'a'.repeat(64),
  outcome: 'succeeded',
  receipt_id: '00000000-0000-4000-8000-00000000e408',
  request_fingerprint: 'b'.repeat(64),
  result_code: 'action_succeeded',
  result_metadata: { changed: true, confidence: 0.8 },
  target: { id: draftLesson.id, type: 'lesson' },
}

let mountedRoot: Root | undefined
let container: HTMLDivElement | undefined
let originalFetch: typeof window.fetch

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'content-type': 'application/json' },
    status,
  })
}

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return `${input.pathname}${input.search}`
  const url = new URL(input.url, window.location.origin)
  return `${url.pathname}${url.search}`
}

async function waitForAssertion(assertion: () => void): Promise<void> {
  let lastError: unknown
  for (let attempt = 0; attempt < 100; attempt += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10))
    })
    try {
      assertion()
      return
    } catch (error) {
      lastError = error
    }
  }
  throw lastError
}

function button(name: string): HTMLButtonElement {
  const match = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(
    (item) => item.textContent?.trim() === name
  )
  if (!match) throw new Error(`Missing button: ${name}`)
  return match
}

async function click(name: string): Promise<void> {
  await act(async () => button(name).click())
}

function setInput(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    'value'
  )?.set
  setter?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

async function renderLessons(queryClient: QueryClient): Promise<void> {
  container = document.createElement('div')
  document.body.append(container)
  mountedRoot = createRoot(container)
  await act(async () => {
    mountedRoot?.render(
      <AppProviders queryClient={queryClient}>
        <LessonsView />
      </AppProviders>
    )
  })
  await waitForAssertion(() => expect(document.body.textContent).toContain(draftLesson.title))
  const details = document.querySelector<HTMLButtonElement>(
    `[aria-label="View lesson details: ${draftLesson.title}"]`
  )
  if (!details) throw new Error('Missing lesson detail button')
  await act(async () => details.click())
}

async function login(): Promise<void> {
  await click('Promote')
  await waitForAssertion(() =>
    expect(document.body.textContent).toMatch(
      /Operator sign in|Restore operator session/
    )
  )
  const token = document.querySelector<HTMLInputElement>('#atlas-bootstrap-token')
  if (!token) throw new Error('Missing bootstrap token input')
  setInput(token, 'component-bootstrap-token-that-is-never-persisted')
  await click('Sign in')
  await waitForAssertion(() =>
    expect(document.body.textContent).not.toContain('Operator sign in')
  )
}

beforeEach(() => {
  originalFetch = window.fetch
})

afterEach(() => {
  mountedRoot?.unmount()
  container?.remove()
  window.fetch = originalFetch
  atlasForgetSession()
  mountedRoot = undefined
  container = undefined
  window.localStorage.clear()
  window.sessionStorage.clear()
})

describe('lesson disposition browser workflow', () => {
  it('logs in without retaining the bootstrap token in query or web storage', async () => {
    const queryClient = createAtlasQueryClient()
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        expect(init.body).toBe(
          JSON.stringify({
            token: 'component-bootstrap-token-that-is-never-persisted',
          })
        )
        return jsonResponse({
          authenticated: true,
          csrf_token: 'component-csrf-secret',
          expires_at: '2099-08-03T10:30:00+00:00',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      return jsonResponse({ lessons: [draftLesson] })
    })

    await renderLessons(queryClient)
    await login()

    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)
    expect(window.location.href).not.toContain('component-bootstrap-token')
    expect(JSON.stringify(queryClient.getQueryData(['atlas', 'session']))).not.toContain(
      'component-bootstrap-token'
    )
    expect(JSON.stringify(queryClient.getQueryData(['atlas', 'session']))).not.toContain(
      'component-csrf-secret'
    )
  })

  it('validates labelled finite confidence before opening a command', async () => {
    let commandCount = 0
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'component-csrf',
          expires_at: '2099-08-03T10:30:00+00:00',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      if (path.endsWith('/promote')) {
        commandCount += 1
      }
      return jsonResponse({ lessons: [draftLesson] })
    })

    await renderLessons(createAtlasQueryClient())
    await login()
    await click('Promote')
    await click('Confirm promotion')

    await waitForAssertion(() =>
      expect(document.body.textContent).toContain(
        'Enter a finite confidence from 0.0 through 1.0.'
      )
    )
    const input = document.querySelector<HTMLInputElement>(
      `#lesson-confidence-${draftLesson.id}`
    )
    expect(input?.labels?.[0]?.textContent).toContain('Operator confidence')
    expect(input?.getAttribute('aria-invalid')).toBe('true')
    expect(commandCount).toBe(0)
  })

  it('sends strict JSON, credentials, CSRF and one idempotency key, then updates the exact lesson cache from success', async () => {
    const queryClient = createAtlasQueryClient()
    let commandRequest: RequestInit | undefined
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'component-csrf',
          expires_at: '2099-08-03T10:30:00+00:00',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      if (path.endsWith('/promote')) {
        commandRequest = init
        return jsonResponse({ lesson: activeLesson, receipt })
      }
      return jsonResponse({ lessons: [draftLesson] })
    })

    await renderLessons(queryClient)
    await login()
    await click('Promote')
    const input = document.querySelector<HTMLInputElement>(
      `#lesson-confidence-${draftLesson.id}`
    )
    if (!input) throw new Error('Missing confidence input')
    setInput(input, '0.8')
    await click('Confirm promotion')

    await waitForAssertion(() => {
      expect(document.body.textContent).toContain('Server receipt')
      expect(document.body.textContent).toContain('action_succeeded')
      expect(document.body.textContent).toContain('No draft lessons')
    })
    expect(commandRequest?.credentials).toBe('same-origin')
    expect(commandRequest?.body).toBe(JSON.stringify({ confidence: 0.8 }))
    const headers = commandRequest?.headers as Record<string, string>
    expect(headers['Content-Type']).toBe('application/json')
    expect(headers['X-Atlas-CSRF']).toBe('component-csrf')
    expect(headers['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
    expect(
      queryClient.getQueryData<{ lessons: LessonItem[] }>([
        'atlas',
        'lessons',
        null,
      ])?.lessons[0]
    ).toEqual(activeLesson)
  })

  it('reuses the retained idempotency key for a safe retry after an ambiguous response', async () => {
    const keys: string[] = []
    let attempt = 0
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'component-csrf',
          expires_at: '2099-08-03T10:30:00+00:00',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      if (path.endsWith('/promote')) {
        keys.push((init?.headers as Record<string, string>)['Idempotency-Key'])
        attempt += 1
        if (attempt === 1) {
          return jsonResponse({ detail: 'ambiguous server failure' }, 500)
        }
        return jsonResponse({ lesson: activeLesson, receipt })
      }
      return jsonResponse({ lessons: [draftLesson] })
    })

    await renderLessons(createAtlasQueryClient())
    await login()
    await click('Promote')
    const input = document.querySelector<HTMLInputElement>(
      `#lesson-confidence-${draftLesson.id}`
    )
    if (!input) throw new Error('Missing confidence input')
    setInput(input, '0.8')
    await click('Confirm promotion')
    await waitForAssertion(() =>
      expect(document.body.textContent).toContain('Retry safely')
    )
    await click('Close')
    expect(document.body.textContent).toContain(draftLesson.title)
    expect(document.body.textContent).toContain('Retry safely')
    await click('Retry safely')
    await waitForAssertion(() =>
      expect(document.body.textContent).toContain('Server receipt')
    )

    expect(keys).toHaveLength(2)
    expect(keys[1]).toBe(keys[0])
  })

  it.each([
    [401, 'Session expired'],
    [403, 'Security refusal'],
  ])('maps HTTP %s to its typed operator recovery', async (status, expected) => {
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'component-csrf',
          expires_at: '2099-08-03T10:30:00+00:00',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: status !== 401, expires_at: null })
      }
      if (path.endsWith('/reject')) {
        return jsonResponse({ detail: 'security policy refused the command' }, status)
      }
      return jsonResponse({ lessons: [draftLesson] })
    })

    await renderLessons(createAtlasQueryClient())
    await login()
    await click('Reject')
    await click('Confirm rejection')
    await waitForAssertion(() => expect(document.body.textContent).toContain(expected))
  })

  it.each([
    [404, 'The server could not find the lesson.'],
    [415, 'Atlas requires strict JSON for this command.'],
  ])('renders the generated HTTP %s error detail', async (status, detail) => {
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'component-csrf',
          expires_at: '2099-08-03T10:30:00+00:00',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      if (path.endsWith('/reject')) {
        return jsonResponse({ detail }, status)
      }
      return jsonResponse({ lessons: [draftLesson] })
    })

    await renderLessons(createAtlasQueryClient())
    await login()
    await click('Reject')
    await click('Confirm rejection')
    await waitForAssertion(() => expect(document.body.textContent).toContain(detail))
  })

  it('shows the safe server lesson on 409 and requires explicit re-review', async () => {
    let conflicted = false
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'component-csrf',
          expires_at: '2099-08-03T10:30:00+00:00',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      if (path.endsWith('/reject')) {
        conflicted = true
        return jsonResponse(
          { detail: 'lesson state changed before disposition committed', lesson: activeLesson },
          409
        )
      }
      return jsonResponse({ lessons: [conflicted ? activeLesson : draftLesson] })
    })

    await renderLessons(createAtlasQueryClient())
    await login()
    await click('Reject')
    await click('Confirm rejection')

    await waitForAssertion(() => {
      expect(document.body.textContent).toContain('Lesson changed; ruling blocked')
      expect(document.body.textContent).toContain('Safe current state: active')
      expect(document.body.textContent).toContain('Close and re-review lesson')
    })
    expect(
      Array.from(document.querySelectorAll('button')).filter((item) =>
        ['Promote', 'Reject'].includes(item.textContent?.trim() ?? '')
      )
    ).toHaveLength(0)
  })

  it('attaches a generated 422 validation message to confidence', async () => {
    window.fetch = vi.fn(async (input, init) => {
      const path = requestPath(input)
      if (path === '/api/v1/session' && init?.method === 'POST') {
        return jsonResponse({
          authenticated: true,
          csrf_token: 'component-csrf',
          expires_at: '2099-08-03T10:30:00+00:00',
        })
      }
      if (path === '/api/v1/session') {
        return jsonResponse({ authenticated: false, expires_at: null })
      }
      if (path.endsWith('/promote')) {
        return jsonResponse(
          {
            detail: [
              {
                input: 0.8,
                loc: ['body', 'confidence'],
                msg: 'Server confidence validation failed',
                type: 'value_error',
              },
            ],
          },
          422
        )
      }
      return jsonResponse({ lessons: [draftLesson] })
    })

    await renderLessons(createAtlasQueryClient())
    await login()
    await click('Promote')
    const input = document.querySelector<HTMLInputElement>(
      `#lesson-confidence-${draftLesson.id}`
    )
    if (!input) throw new Error('Missing confidence input')
    setInput(input, '0.8')
    await click('Confirm promotion')

    await waitForAssertion(() =>
      expect(document.body.textContent).toContain(
        'Server confidence validation failed'
      )
    )
    expect(input.getAttribute('aria-invalid')).toBe('true')
  })
})
