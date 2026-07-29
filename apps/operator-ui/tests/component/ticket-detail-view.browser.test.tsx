import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import type { components } from '@/api/atlas-openapi'
import {
  TicketDetailContent,
  TicketEvidenceTab,
} from '@/features/tickets/ticket-detail-view'

type TicketDetail = components['schemas']['TicketDetailResponse']
type TicketDependencies = components['schemas']['TicketDependenciesResponse']
type TicketEvidenceItem = components['schemas']['TicketEvidenceItemSchema']

let mountedRoot: Root | undefined
let container: HTMLDivElement | undefined

const danglingTarget = '00000000-0000-4000-8000-00000000d393'

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

const evidenceRecords: TicketEvidenceItem[] = [
  {
    has_system_pin_triple: false,
    status: 'pending',
    tier: 'agent',
    type: 'manual_approval',
  },
  {
    has_system_pin_triple: true,
    status: 'passed',
    tier: 'system',
    type: 'test_result',
  },
]

const readyDependencies: TicketDependencies = {
  blocked_by: [],
  blockers: [],
  key: 'ATLAS-1',
  readiness: {
    ready: true,
    reasons: [],
  },
}

const multiReasonDependencies: TicketDependencies = {
  blocked_by: ['ATLAS-4'],
  blockers: [
    { code: 'dependency_not_done', key: 'ATLAS-3' },
    { code: 'adr_not_accepted', key: 'ADR-0031' },
    { code: 'dangling_target', key: danglingTarget },
  ],
  key: 'ATLAS-2',
  readiness: {
    ready: false,
    reasons: [
      {
        code: 'wrong_status',
        message: "status 'in_progress' is not one of ['backlog', 'planned']",
        status: 'in_progress',
        target: null,
      },
      {
        code: 'dependency_not_done',
        message: "depends_on ticket 'ATLAS-3' has status 'planned', not 'done'",
        status: 'planned',
        target: 'ATLAS-3',
      },
      {
        code: 'adr_not_accepted',
        message:
          "depends_on ADR 'ADR-0031' has status 'proposed', not 'accepted'",
        status: 'proposed',
        target: 'ADR-0031',
      },
      {
        code: 'dangling_target',
        message: `depends_on target '${danglingTarget}' is missing (dangling)`,
        status: null,
        target: danglingTarget,
      },
      {
        code: 'no_acceptance_criteria',
        message: 'ticket has no acceptance criteria',
        status: null,
        target: null,
      },
    ],
  },
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

function testTexts(testId: string): string[] {
  return Array.from(document.querySelectorAll(`[data-testid="${testId}"]`)).map(
    (element) => element.textContent?.trim() ?? ''
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

  it('renders the shared empty state for an evidence-free ticket', async () => {
    await render(<TicketDetailContent ticket={baseTicket} />)

    for (const tabName of ['Definition', 'Metadata', 'Evidence', 'Dependencies']) {
      expect(document.body.textContent).toContain(tabName)
    }

    await selectTab('Evidence')
    expect(testText('ticket-detail-evidence-panel')).toContain(
      'No evidence stored'
    )
    expect(document.querySelector('[role="alert"]')).toBeNull()

    await selectTab('Dependencies')
    expect(testText('ticket-detail-dependencies-panel')).toBe('')
  })

  it('renders prominent complete and incomplete pin-triple states', async () => {
    await render(
      <TicketEvidenceTab
        state={{ kind: 'success', evidence: evidenceRecords }}
      />
    )

    const pinStates = Array.from(
      document.querySelectorAll('[data-testid="ticket-evidence-pin-state"]')
    )

    expect(testTexts('ticket-evidence-pin-state-label')).toEqual([
      'System pin triple incomplete',
      'System pin triple complete',
    ])
    expect(pinStates.map((element) => element.getAttribute('data-pin-state'))).toEqual(
      ['incomplete', 'complete']
    )
    expect(pinStates[0].className).toContain('border-destructive')
    expect(pinStates[1].className).toContain('border-primary')
  })

  it('visually distinguishes agent-tier and system-tier evidence records', async () => {
    await render(
      <TicketEvidenceTab
        state={{ kind: 'success', evidence: evidenceRecords }}
      />
    )

    expect(testTexts('ticket-evidence-tier')).toEqual(['agent', 'system'])

    const tierBadges = Array.from(
      document.querySelectorAll('[data-testid="ticket-evidence-tier"]')
    )

    expect(tierBadges[0].getAttribute('data-tier')).toBe('agent')
    expect(tierBadges[1].getAttribute('data-tier')).toBe('system')
    expect(tierBadges[0].className).not.toBe(tierBadges[1].className)
    expect(tierBadges[0].className).toContain('bg-muted')
    expect(tierBadges[1].className).toContain('bg-primary')
  })

  it('does not render an interactive raw-payload affordance in the evidence tab', async () => {
    await render(
      <TicketEvidenceTab
        state={{ kind: 'success', evidence: evidenceRecords }}
      />
    )

    const interactiveElements = Array.from(
      document.querySelectorAll(
        'a, button, details, input, select, summary, textarea'
      )
    )

    expect(interactiveElements).toEqual([])
  })

  it('renders every not-ready reason, dependency links, and dangling defects', async () => {
    await render(
      <TicketDetailContent
        dependencies={multiReasonDependencies}
        ticket={{ ...baseTicket, key: 'ATLAS-2' }}
      />
    )

    await selectTab('Dependencies')

    expect(testText('ticket-detail-readiness-verdict')).toBe('Not ready')
    expect(testTexts('ticket-detail-readiness-reason-code')).toEqual(
      multiReasonDependencies.readiness.reasons.map((reason) => reason.code)
    )
    expect(testTexts('ticket-detail-readiness-reason-label')).toEqual([
      'Wrong status',
      'Dependency not done',
      'ADR not accepted',
      'Dangling target',
      'No acceptance criteria',
    ])
    expect(testText('ticket-detail-dependencies-panel')).toContain('ATLAS-3')
    expect(testText('ticket-detail-dependencies-panel')).toContain('planned')
    expect(testText('ticket-detail-dependencies-panel')).toContain('ADR-0031')
    expect(testText('ticket-detail-dependencies-panel')).toContain('proposed')
    expect(testText('ticket-detail-dependencies-panel')).toContain(danglingTarget)
    expect(testText('ticket-detail-blocker-defect-target')).toBe(danglingTarget)
    expect(
      document
        .querySelector('[data-testid="ticket-detail-blocker-link"]')
        ?.getAttribute('href')
    ).toBe('/tickets/ATLAS-3')
    expect(
      document
        .querySelector('[data-testid="ticket-detail-blocked-by-link"]')
        ?.getAttribute('href')
    ).toBe('/tickets/ATLAS-4')
    expect(document.body.textContent).not.toMatch(/dispatch/i)
  })

  it('renders a ready verdict with no not-ready reason list', async () => {
    await render(
      <TicketDetailContent dependencies={readyDependencies} ticket={baseTicket} />
    )

    await selectTab('Dependencies')

    expect(testText('ticket-detail-readiness-verdict')).toBe('Ready')
    expect(
      document.querySelector('[data-testid="ticket-detail-readiness-reasons"]')
    ).toBeNull()
    expect(testText('ticket-detail-blockers-empty')).toBe('None')
    expect(testText('ticket-detail-blocked-by-empty')).toBe('None')
  })
})
