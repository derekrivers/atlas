import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import type { components } from '@/api/atlas-openapi'
import { TicketDetailContent } from '@/features/tickets/ticket-detail-view'

type TicketDetail = components['schemas']['TicketDetailResponse']

let mountedRoot: Root | undefined
let container: HTMLDivElement | undefined

const baseTicket: TicketDetail = {
  acceptance_criteria: [
    'First stored criterion renders in order.',
    'Second stored criterion renders in order.',
  ],
  completed_at: '2026-07-26T18:43:42Z',
  component: 'operator-ui',
  context: 'A stored context paragraph for the ticket.',
  created_at: '2026-07-26T18:43:42Z',
  definition_of_done: [
    'The definition view is complete.',
    'The metadata view is complete.',
  ],
  documentation_requirements: [
    'Document only behavior that diverges from canonical docs.',
    'Leave generated planning renders untouched.',
  ],
  estimated_effort: 3,
  external_github_issue_id: 'GH-seeded-detail',
  external_linear_id: 'LIN-seeded-detail',
  implementation_notes: [
    'Use the stored detail projection directly.',
    'Keep the evidence request separate.',
  ],
  key: 'ATLAS-1',
  non_goals: [
    'Do not add writes.',
    'Do not assemble cross-resource state server-side.',
  ],
  objective: 'Render one ticket definition and metadata.',
  priority: 1,
  relevant_docs: ['docs/atlas/operator-ui.md', 'docs/atlas/operator-api.md'],
  risk_level: 'low',
  source_anchor: 'docs/atlas/operator-ui.md#ticket-detail-ticketskey',
  status: 'done',
  tags: ['operator-ui', 'detail'],
  test_requirements: [
    'Cover list rendering in browser mode.',
    'Cover live API rendering in Playwright.',
  ],
  ticket_type: 'feature',
  title: 'Ticket detail view',
  updated_at: '2026-07-26T18:43:42Z',
}

async function render(component: ReactNode) {
  container = document.createElement('div')
  document.body.append(container)
  mountedRoot = createRoot(container)

  await act(async () => {
    mountedRoot?.render(component)
  })
}

function itemTexts(testId: string): string[] {
  return Array.from(
    document.querySelectorAll(`[data-testid="${testId}-item"]`)
  ).map((element) => element.textContent ?? '')
}

function testText(testId: string): string {
  return (
    document.querySelector(`[data-testid="${testId}"]`)?.textContent?.trim() ??
    ''
  )
}

async function selectTab(name: string) {
  const tab = Array.from(document.querySelectorAll('button')).find(
    (button) => button.textContent === name
  )

  if (!tab) {
    throw new Error(`Missing ${name} tab`)
  }

  await act(async () => {
    tab.dispatchEvent(
      new MouseEvent('mousedown', { bubbles: true, button: 0, cancelable: true })
    )
    tab.dispatchEvent(
      new MouseEvent('mouseup', { bubbles: true, button: 0, cancelable: true })
    )
    tab.dispatchEvent(
      new MouseEvent('click', { bubbles: true, button: 0, cancelable: true })
    )
  })
}

afterEach(() => {
  mountedRoot?.unmount()
  container?.remove()
  mountedRoot = undefined
  container = undefined
})

describe('ticket detail view rendering', () => {
  it('renders definition fields and preserves stored list order', async () => {
    await render(<TicketDetailContent ticket={baseTicket} />)

    expect(testText('ticket-detail-key')).toBe(baseTicket.key)
    expect(testText('ticket-detail-title')).toBe(baseTicket.title)
    expect(testText('ticket-detail-objective')).toBe(baseTicket.objective)
    expect(testText('ticket-detail-context')).toBe(baseTicket.context)
    expect(itemTexts('ticket-detail-relevant-docs')).toEqual(
      baseTicket.relevant_docs
    )
    expect(itemTexts('ticket-detail-acceptance-criteria')).toEqual(
      baseTicket.acceptance_criteria
    )
    expect(itemTexts('ticket-detail-non-goals')).toEqual(baseTicket.non_goals)
    expect(itemTexts('ticket-detail-implementation-notes')).toEqual(
      baseTicket.implementation_notes
    )
    expect(itemTexts('ticket-detail-test-requirements')).toEqual(
      baseTicket.test_requirements
    )
    expect(itemTexts('ticket-detail-documentation-requirements')).toEqual(
      baseTicket.documentation_requirements
    )
    expect(itemTexts('ticket-detail-definition-of-done')).toEqual(
      baseTicket.definition_of_done
    )
  })

  it('renders metadata fields, source anchor, external IDs, and null empties', async () => {
    await render(
      <TicketDetailContent
        ticket={{
          ...baseTicket,
          completed_at: null,
          component: null,
          estimated_effort: null,
          tags: [],
        }}
      />
    )

    await selectTab('Metadata')

    expect(testText('ticket-detail-status')).toBe(baseTicket.status)
    expect(testText('ticket-detail-ticket-type')).toBe(baseTicket.ticket_type)
    expect(testText('ticket-detail-risk-level')).toBe(baseTicket.risk_level)
    expect(testText('ticket-detail-priority')).toBe(String(baseTicket.priority))
    expect(testText('ticket-detail-estimated-effort')).toBe('None')
    expect(testText('ticket-detail-component')).toBe('None')
    expect(testText('ticket-detail-tags-empty')).toBe('None')
    expect(testText('ticket-detail-source-anchor')).toBe(baseTicket.source_anchor)
    expect(testText('ticket-detail-external-linear-id')).toBe(
      baseTicket.external_linear_id
    )
    expect(testText('ticket-detail-external-github-issue-id')).toBe(
      baseTicket.external_github_issue_id
    )
    expect(testText('ticket-detail-created-at')).toBe(baseTicket.created_at)
    expect(testText('ticket-detail-updated-at')).toBe(baseTicket.updated_at)
    expect(testText('ticket-detail-completed-at')).toBe('None')
  })

  it('exposes evidence and dependencies tabs as empty panels', async () => {
    await render(<TicketDetailContent ticket={baseTicket} />)

    for (const tabName of ['Definition', 'Metadata', 'Evidence', 'Dependencies']) {
      expect(document.body.textContent).toContain(tabName)
    }

    await selectTab('Evidence')
    expect(testText('ticket-detail-evidence-panel')).toBe('')

    await selectTab('Dependencies')
    expect(testText('ticket-detail-dependencies-panel')).toBe('')
  })
})
