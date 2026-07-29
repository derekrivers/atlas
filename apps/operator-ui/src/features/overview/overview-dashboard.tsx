import { Link } from '@tanstack/react-router'
import {
  Activity,
  ArrowRight,
  ClipboardList,
  GitBranch,
  Inbox,
  ShieldCheck,
} from 'lucide-react'
import {
  useDependencyCriticalPathQuery,
  useReviewsQuery,
  useSystemStatusQuery,
  useTicketsQuery,
} from '@/api/query-hooks'
import { ticketDetailHref } from '@/app-shell/surfaces'
import { Main } from '@/components/layout/main'
import { StalenessIndicator } from '@/components/staleness-indicator'
import {
  LoadingState,
  RequestErrorState,
} from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import {
  formatTicketBoardLabel,
  selectTicketStatusDistribution,
  selectTicketStatusDistributionTotal,
  type TicketStatusDistributionBucket,
} from '@/features/tickets/ticket-board-state'
import {
  selectCriticalPathHead,
  selectCriticalPathTotalEffort,
} from '@/features/critical-path/selectors'
import { selectReviewQueueDepth } from '@/features/reviews/selectors'

type StatTileProps = {
  icon: React.ElementType
  label: string
  testId: string
  value: number
}

function StatTile({ icon: Icon, label, testId, value }: StatTileProps) {
  return (
    <div className='border-border bg-card text-card-foreground rounded-lg border p-4'>
      <div className='flex items-center justify-between gap-3'>
        <p className='text-muted-foreground text-sm font-medium'>{label}</p>
        <Icon aria-hidden='true' className='text-muted-foreground size-4' />
      </div>
      <div
        data-testid={testId}
        className='mt-3 font-mono text-3xl font-semibold tabular-nums'
      >
        {value}
      </div>
    </div>
  )
}

function StatusBucket({
  bucket,
  maxCount,
}: {
  bucket: TicketStatusDistributionBucket
  maxCount: number
}) {
  const width = maxCount > 0 ? `${Math.max(8, (bucket.count / maxCount) * 100)}%` : '0%'

  return (
    <li
      data-status={bucket.status}
      data-testid='overview-status-bucket'
      className='grid gap-2'
    >
      <div className='flex items-center justify-between gap-3 text-sm'>
        <span className='font-medium'>{formatTicketBoardLabel(bucket.status)}</span>
        <span
          data-testid='overview-status-bucket-count'
          className='font-mono tabular-nums'
        >
          {bucket.count}
        </span>
      </div>
      <div
        aria-hidden='true'
        className='bg-muted h-2 overflow-hidden rounded-sm'
      >
        <div
          className='bg-primary h-full rounded-sm'
          style={{ width }}
        />
      </div>
    </li>
  )
}

function StatusDistribution({
  apiTicketCount,
  distribution,
  total,
}: {
  apiTicketCount: number
  distribution: TicketStatusDistributionBucket[]
  total: number
}) {
  const maxCount = Math.max(0, ...distribution.map((bucket) => bucket.count))
  const hasMismatch = total !== apiTicketCount

  return (
    <section
      aria-labelledby='overview-status-distribution-heading'
      className='border-border bg-card text-card-foreground rounded-lg border p-4'
    >
      <div className='flex flex-col gap-3 border-b pb-4 sm:flex-row sm:items-start sm:justify-between'>
        <div>
          <h2
            id='overview-status-distribution-heading'
            className='text-base font-semibold tracking-normal'
          >
            Board Composition
          </h2>
          <p className='text-muted-foreground mt-1 text-sm'>
            Derived from the complete board response.
          </p>
        </div>
        <Badge
          data-testid='overview-status-distribution-total'
          data-total={total}
          variant='outline'
          className={cn(hasMismatch && 'border-destructive/40 text-destructive')}
        >
          {total} total
        </Badge>
      </div>

      {hasMismatch ? (
        <div
          role='alert'
          className='border-destructive/40 text-destructive mt-4 rounded-lg border p-3 text-sm'
        >
          Complete board total differs from the API ticket count.
        </div>
      ) : null}

      <ul
        data-testid='overview-status-distribution'
        className='mt-4 grid gap-4 sm:grid-cols-2'
      >
        {distribution.map((bucket) => (
          <StatusBucket
            bucket={bucket}
            key={bucket.status}
            maxCount={maxCount}
          />
        ))}
      </ul>
    </section>
  )
}

function CriticalPathHead({
  head,
}: {
  head: ReturnType<typeof selectCriticalPathHead>
}) {
  if (!head) {
    return (
      <span
        data-testid='overview-critical-path-head'
        className='text-muted-foreground text-sm font-medium'
      >
        No critical path
      </span>
    )
  }

  return (
    <Link
      to={ticketDetailHref(head.key)}
      data-testid='overview-critical-path-head-link'
      className='text-primary inline-flex items-center gap-1 font-medium underline-offset-4 hover:underline'
    >
      {head.key}
      <ArrowRight aria-hidden='true' className='size-3.5' />
    </Link>
  )
}

export function OverviewDashboardRoute() {
  const statusQuery = useSystemStatusQuery()
  const ticketsQuery = useTicketsQuery()
  const reviewsQuery = useReviewsQuery()
  const criticalPathQuery = useDependencyCriticalPathQuery()

  const requestError =
    statusQuery.error ??
    ticketsQuery.error ??
    reviewsQuery.error ??
    criticalPathQuery.error

  if (
    statusQuery.isPending ||
    ticketsQuery.isPending ||
    reviewsQuery.isPending ||
    criticalPathQuery.isPending
  ) {
    return (
      <Main>
        <LoadingState label='Loading overview dashboard' />
      </Main>
    )
  }

  if (requestError) {
    return (
      <Main>
        <RequestErrorState
          error={requestError}
          title='Overview request failed'
        />
      </Main>
    )
  }

  if (
    !statusQuery.data ||
    !ticketsQuery.data ||
    !reviewsQuery.data ||
    !criticalPathQuery.data
  ) {
    return (
      <Main>
        <LoadingState label='Loading overview dashboard' />
      </Main>
    )
  }

  const statusDistribution = selectTicketStatusDistribution(
    ticketsQuery.data.tickets
  )
  const statusDistributionTotal =
    selectTicketStatusDistributionTotal(statusDistribution)
  const reviewDepth = selectReviewQueueDepth(reviewsQuery.data)
  const criticalPathHead = selectCriticalPathHead(criticalPathQuery.data)
  const criticalPathTotalEffort = selectCriticalPathTotalEffort(
    criticalPathQuery.data
  )

  return (
    <Main>
      <div
        data-testid='overview-dashboard'
        className='flex flex-col gap-6'
      >
        <div className='flex flex-col gap-4 border-b pb-6 xl:flex-row xl:items-end xl:justify-between'>
          <div className='space-y-2'>
            <p className='text-muted-foreground text-sm font-medium'>Overview</p>
            <h1 className='text-2xl font-semibold tracking-normal'>
              Operational Snapshot
            </h1>
            <div className='text-muted-foreground flex flex-wrap gap-x-3 gap-y-1 text-sm'>
              <span>Atlas {statusQuery.data.package_version}</span>
              <span aria-hidden='true'>/</span>
              <span>
                Schema {statusQuery.data.schema_revision ?? 'unrecorded'}
              </span>
            </div>
          </div>
          <div className='grid gap-2 md:grid-cols-2'>
            <StalenessIndicator
              label='Linear sync'
              testId='overview-linear-sync-staleness'
              value={statusQuery.data.last_linear_sync_at}
            />
            <StalenessIndicator
              label='Evidence pull'
              testId='overview-evidence-pull-staleness'
              value={statusQuery.data.last_evidence_pull_at}
            />
          </div>
        </div>

        <section
          aria-label='Overview status metrics'
          className='grid gap-3 sm:grid-cols-2 xl:grid-cols-4'
        >
          <StatTile
            icon={ClipboardList}
            label='Tickets'
            testId='overview-ticket-count'
            value={statusQuery.data.ticket_count}
          />
          <StatTile
            icon={ShieldCheck}
            label='Evidence'
            testId='overview-evidence-count'
            value={statusQuery.data.evidence_count}
          />
          <StatTile
            icon={Inbox}
            label='Review Queue'
            testId='overview-review-depth'
            value={reviewDepth}
          />
          <StatTile
            icon={GitBranch}
            label='Critical Path Effort'
            testId='overview-critical-path-total-effort'
            value={criticalPathTotalEffort}
          />
        </section>

        <div className='grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(20rem,0.85fr)]'>
          <StatusDistribution
            apiTicketCount={statusQuery.data.ticket_count}
            distribution={statusDistribution}
            total={statusDistributionTotal}
          />

          <section
            aria-labelledby='overview-critical-path-heading'
            className='border-border bg-card text-card-foreground rounded-lg border p-4'
          >
            <div className='flex items-start justify-between gap-3 border-b pb-4'>
              <div>
                <h2
                  id='overview-critical-path-heading'
                  className='text-base font-semibold tracking-normal'
                >
                  Critical Path Head
                </h2>
                <p className='text-muted-foreground mt-1 text-sm'>
                  First item in the API execution order.
                </p>
              </div>
              <Activity aria-hidden='true' className='text-muted-foreground size-4' />
            </div>
            <div className='mt-4 grid gap-3'>
              <div>
                <p className='text-muted-foreground text-sm font-medium'>Ticket</p>
                <div className='mt-1'>
                  <CriticalPathHead head={criticalPathHead} />
                </div>
              </div>
              <div>
                <p className='text-muted-foreground text-sm font-medium'>
                  Total effort
                </p>
                <p className='mt-1 font-mono text-2xl font-semibold tabular-nums'>
                  {criticalPathTotalEffort}
                </p>
              </div>
            </div>
          </section>
        </div>
      </div>
    </Main>
  )
}
