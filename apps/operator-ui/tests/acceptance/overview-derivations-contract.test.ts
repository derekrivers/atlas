import { Linter } from 'eslint'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import tseslint from 'typescript-eslint'
import { describe, expect, it } from 'vitest'
// @ts-expect-error eslint.config.js intentionally remains JavaScript.
import { atlasPlugin } from '../../eslint.config.js'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const overviewPath = join(
  appRoot,
  'src',
  'features',
  'overview',
  'overview-dashboard.tsx'
)

const requiredSelectorImports = `
  import {
    selectTicketStatusDistribution,
    selectTicketStatusDistributionTotal,
  } from '@/features/tickets/ticket-board-state'
  import { selectReviewQueueDepth } from '@/features/reviews/selectors'
  import {
    selectCriticalPathHead,
    selectCriticalPathTotalEffort,
  } from '@/features/critical-path/selectors'
`

function verifyOverviewDerivationRule(source: string) {
  const linter = new Linter({ configType: 'flat' })
  return linter.verify(
    source,
    [
      {
        files: ['**/*.tsx'],
        languageOptions: {
          ecmaVersion: 2022,
          parser: tseslint.parser,
          parserOptions: {
            ecmaFeatures: { jsx: true },
            sourceType: 'module',
          },
        },
        plugins: {
          atlas: atlasPlugin,
        },
        rules: {
          'atlas/overview-shared-derivations': 'error',
        },
      },
    ],
    { filename: overviewPath }
  )
}

describe('overview derivation contract', () => {
  it('imports overview aggregates from the owning feature selectors', () => {
    const source = readFileSync(overviewPath, 'utf8')

    expect(source).toContain(
      "selectTicketStatusDistribution,\n  selectTicketStatusDistributionTotal,"
    )
    expect(source).toContain("from '@/features/tickets/ticket-board-state'")
    expect(source).toContain("selectReviewQueueDepth } from '@/features/reviews/selectors'")
    expect(source).toContain("selectCriticalPathHead,\n  selectCriticalPathTotalEffort,")
    expect(source).toContain("from '@/features/critical-path/selectors'")
  })

  it('keeps time-series primitives out of the overview dashboard source', () => {
    const source = readFileSync(overviewPath, 'utf8')

    expect(source).not.toMatch(
      /\b(LineChart|AreaChart|BarChart|ResponsiveContainer|Sparkline|XAxis|YAxis|recharts|canvas|time series|trend)\b/i
    )
  })

  it('reports duplicated overview derivations from API response surfaces', () => {
    const messages = verifyOverviewDerivationRule(
      `
        import { useDependencyCriticalPathQuery, useReviewsQuery, useTicketsQuery } from '@/api/query-hooks'
        ${requiredSelectorImports}

        export function OverviewDashboardRoute() {
          const ticketsQuery = useTicketsQuery()
          const reviewsQuery = useReviewsQuery()
          const pathQuery = useDependencyCriticalPathQuery()
          const statusCounts = ticketsQuery.data?.tickets.reduce((counts, ticket) => counts + Number(Boolean(ticket.status)), 0)
          const localTickets = ticketsQuery.data?.tickets
          let touchedTickets = 0
          localTickets?.forEach((ticket) => {
            if (ticket.status) {
              touchedTickets += 1
            }
          })
          const reviewDepth = reviewsQuery.data?.reviews.length
          const pathSteps = pathQuery.data?.steps.filter(Boolean)
          const effort = pathQuery.data?.total_effort
          return <div>{statusCounts}{touchedTickets}{reviewDepth}{pathSteps}{effort}</div>
        }
      `
    )

    const messageText = messages.map((message) => message.message)

    expect(
      messageText.filter(
        (message) =>
          message ===
          'Overview dashboard aggregates must use board, review queue, and critical path selectors instead of local derivations.'
      ).length
    ).toBeGreaterThanOrEqual(4)
    expect(messageText).not.toContain(
      'Overview dashboard must import selectTicketStatusDistribution from @/features/tickets/ticket-board-state.'
    )
  })

  it('allows presentation-only collection operations in the overview dashboard', () => {
    const messages = verifyOverviewDerivationRule(
      `
        ${requiredSelectorImports}

        export function OverviewDashboardRoute() {
          const navigationLabels = ['Overview', 'Board', 'Reviews']
          const visibleNavigationLabels = navigationLabels.filter(Boolean)
          let observedLabelCount = 0
          visibleNavigationLabels.forEach((label) => {
            if (label.length > 0) {
              observedLabelCount += 1
            }
          })
          const labelText = visibleNavigationLabels.reduce(
            (text, label) => text + ' ' + label,
            ''
          )
          return <div>{labelText}{observedLabelCount}</div>
        }
      `
    )

    expect(messages).toEqual([])
  })
})
