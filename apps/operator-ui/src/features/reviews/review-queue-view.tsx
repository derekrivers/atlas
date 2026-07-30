import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Clock3,
  MinusCircle,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { atlasOpenApiEnums } from '@/api/atlas-openapi-runtime'
import { useReviewsQuery } from '@/api/query-hooks'
import type { components } from '@/api/atlas-openapi'
import { ticketDetailHref } from '@/app-shell/surfaces'
import { Main } from '@/components/layout/main'
import {
  EmptyCollectionState,
  LoadingState,
  RequestErrorState,
} from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import {
  selectReviewQueueDepth,
  selectReviewQueueItems,
} from './selectors'

type Schema = components['schemas']
type ReviewItem = Schema['ReviewQueueItemSchema']
type ReviewCheck = Schema['ReviewCheckSchema']
type EvidenceStatus = Schema['EvidenceStatus']
type VerificationCheckType = Schema['VerificationCheckType']
type DisplayCheckStatus = EvidenceStatus | 'not_run'

const verificationCheckTypes: readonly VerificationCheckType[] =
  atlasOpenApiEnums.VerificationCheckType

const checkStatusDisplay = {
  failed: {
    icon: XCircle,
    label: 'Failed',
    className: 'border-destructive/40 text-destructive',
  },
  not_applicable: {
    icon: MinusCircle,
    label: 'Not applicable',
    className: 'border-border text-muted-foreground',
  },
  not_run: {
    icon: CircleDashed,
    label: 'Never run',
    className: 'border-border border-dashed text-muted-foreground',
  },
  passed: {
    icon: CheckCircle2,
    label: 'Passed',
    className: 'border-primary/40 text-primary',
  },
  pending: {
    icon: Clock3,
    label: 'Pending',
    className: 'border-border text-muted-foreground',
  },
  warning: {
    icon: AlertTriangle,
    label: 'Warning',
    className: 'border-border text-muted-foreground',
  },
} as const satisfies Record<
  DisplayCheckStatus,
  { className: string; icon: LucideIcon; label: string }
>

function labelFromValue(value: string): string {
  return value
    .split('_')
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(' ')
}

function latestCheckStatuses(
  checks: readonly ReviewCheck[]
): ReadonlyMap<VerificationCheckType, EvidenceStatus> {
  const statuses = new Map<VerificationCheckType, EvidenceStatus>()
  for (const check of checks) {
    statuses.set(check.check_type, check.status)
  }
  return statuses
}

function CheckStateBadge({ status }: { status: DisplayCheckStatus }) {
  const display = checkStatusDisplay[status]
  const Icon = display.icon

  return (
    <span
      className={cn(
        'inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-xs font-medium whitespace-nowrap',
        display.className
      )}
    >
      <Icon aria-hidden='true' className='size-3.5' />
      {display.label}
    </span>
  )
}

function GateSignal({
  label,
  passed,
  testId,
}: {
  label: string
  passed: boolean
  testId: string
}) {
  const Icon = passed ? CheckCircle2 : XCircle
  return (
    <div
      data-gate-state={passed ? 'pass' : 'fail'}
      data-testid={testId}
      className={cn(
        'rounded-lg border p-4',
        passed
          ? 'border-primary/40 text-primary'
          : 'border-destructive/40 text-destructive'
      )}
    >
      <div className='flex items-center justify-between gap-3'>
        <span className='text-foreground text-sm font-medium'>{label}</span>
        <span className='inline-flex items-center gap-1.5 text-xs font-semibold uppercase'>
          <Icon aria-hidden='true' className='size-4' />
          {passed ? 'Pass' : 'Fail'}
        </span>
      </div>
    </div>
  )
}

function VerdictBadge({ verdict }: { verdict: EvidenceStatus }) {
  return (
    <Badge
      variant={verdict === 'failed' ? 'destructive' : 'outline'}
      className='h-7 px-2.5'
    >
      Verdict {labelFromValue(verdict)}
    </Badge>
  )
}

export function ReviewChecksMatrix({
  checks,
}: {
  checks: readonly ReviewCheck[]
}) {
  const statuses = latestCheckStatuses(checks)

  return (
    <div
      aria-label='Verification checks'
      className='border-border overflow-hidden rounded-lg border'
      role='table'
    >
      <div role='rowgroup'>
        {verificationCheckTypes.map((checkType) => {
          const status = statuses.get(checkType) ?? 'not_run'
          return (
            <div
              data-check-state={status}
              data-check-type={checkType}
              data-testid='review-check-row'
              key={checkType}
              role='row'
              className='grid min-h-11 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b px-3 py-2 last:border-b-0'
            >
              <span role='cell' className='min-w-0 text-sm font-medium'>
                {labelFromValue(checkType)}
              </span>
              <span role='cell'>
                <CheckStateBadge status={status} />
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function ReviewQueueItemCard({ review }: { review: ReviewItem }) {
  return (
    <article
      data-testid='review-queue-item'
      data-ticket-key={review.key}
      className='border-border bg-card text-card-foreground rounded-lg border p-4'
    >
      <div className='flex flex-col gap-3 border-b pb-4 sm:flex-row sm:items-start sm:justify-between'>
        <div className='min-w-0'>
          <a
            href={ticketDetailHref(review.key)}
            className='text-foreground hover:text-primary text-base font-semibold underline-offset-4 hover:underline'
          >
            {review.key} {review.title}
          </a>
          <div className='text-muted-foreground mt-2 flex flex-wrap gap-2 text-xs'>
            <span>{labelFromValue(review.status)}</span>
            <span aria-hidden='true'>/</span>
            <span>{labelFromValue(review.ticket_type)}</span>
          </div>
        </div>
        <VerdictBadge verdict={review.verdict} />
      </div>

      <div className='mt-4 grid gap-3 lg:grid-cols-[minmax(18rem,0.9fr)_minmax(0,1.4fr)]'>
        <section aria-label={`Acceptance gates for ${review.key}`}>
          <div className='grid gap-3 sm:grid-cols-2 lg:grid-cols-1'>
            <GateSignal
              label='System evidence'
              passed={review.has_system_evidence}
              testId='review-gate-system-evidence'
            />
            <GateSignal
              label='PR merged evidence'
              passed={review.has_pr_merged_evidence}
              testId='review-gate-pr-merged-evidence'
            />
          </div>
        </section>

        <section aria-label={`Verification checks for ${review.key}`}>
          <ReviewChecksMatrix checks={review.checks} />
        </section>
      </div>
    </article>
  )
}

function ReviewQueueContent({ reviews }: { reviews: readonly ReviewItem[] }) {
  if (reviews.length === 0) {
    return (
      <EmptyCollectionState
        title='No tickets awaiting review'
        detail='The review queue is empty.'
      />
    )
  }

  return (
    <section aria-label='Tickets awaiting review' className='grid gap-4'>
      {reviews.map((review) => (
        <ReviewQueueItemCard key={review.key} review={review} />
      ))}
    </section>
  )
}

export function ReviewQueueView() {
  const reviewsQuery = useReviewsQuery()

  if (reviewsQuery.isPending) {
    return (
      <Main>
        <LoadingState label='Loading review queue' />
      </Main>
    )
  }

  if (reviewsQuery.isError) {
    return (
      <Main>
        <RequestErrorState
          error={reviewsQuery.error}
          title='Review queue request failed'
        />
      </Main>
    )
  }

  const reviews = selectReviewQueueItems(reviewsQuery.data)
  const reviewDepth = selectReviewQueueDepth(reviewsQuery.data)

  return (
    <Main>
      <div className='flex flex-col gap-6'>
        <div className='flex flex-col gap-3 border-b pb-6 sm:flex-row sm:items-end sm:justify-between'>
          <div className='space-y-1'>
            <p className='text-muted-foreground text-sm font-medium'>
              Review Queue
            </p>
            <h1 className='text-2xl font-semibold tracking-normal'>
              Acceptance Review
            </h1>
          </div>
          <Badge variant='outline' className='w-fit'>
            {reviewDepth} waiting
          </Badge>
        </div>
        <ReviewQueueContent reviews={reviews} />
      </div>
    </Main>
  )
}
