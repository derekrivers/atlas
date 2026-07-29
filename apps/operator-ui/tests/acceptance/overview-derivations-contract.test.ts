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

  it('reports duplicated overview derivations through eslint', async () => {
    const linter = new Linter({ configType: 'flat' })
    const messages = linter.verify(
      `
        import { useDependencyCriticalPathQuery, useReviewsQuery, useTicketsQuery } from '@/api/query-hooks'

        export function OverviewDashboardRoute() {
          const ticketsQuery = useTicketsQuery()
          const reviewsQuery = useReviewsQuery()
          const pathQuery = useDependencyCriticalPathQuery()
          const statusCounts = ticketsQuery.data?.tickets.reduce((counts, ticket) => counts + Number(Boolean(ticket.status)), 0)
          const reviewDepth = reviewsQuery.data?.reviews.length
          const effort = pathQuery.data?.total_effort
          return <div>{statusCounts}{reviewDepth}{effort}</div>
        }
      `,
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

    const messageText = messages.map((message) => message.message)

    expect(messageText).toContain(
      'Overview dashboard aggregates must use board, review queue, and critical path selectors instead of local derivations.'
    )
    expect(messageText).toContain(
      'Overview dashboard must import selectTicketStatusDistribution from @/features/tickets/ticket-board-state.'
    )
  })
})
