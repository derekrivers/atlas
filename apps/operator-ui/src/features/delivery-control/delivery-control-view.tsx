import { useEffect, useRef } from 'react'
import { AlertTriangle, CircleGauge, RefreshCw, ShieldCheck } from 'lucide-react'
import { Link } from '@tanstack/react-router'
import type { components } from '@/api/atlas-openapi'
import { AtlasRequestError } from '@/api/client'
import { useDeliveryControlQuery } from '@/api/query-hooks'
import { ticketDetailHref } from '@/app-shell/surfaces'
import { Main } from '@/components/layout/main'
import { LoadingState, RequestErrorState } from '@/components/states'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { useOperatorSession } from '@/context/operator-session-provider'
import { PolicyEditor } from '@/features/delivery-control/policy-editor'
import { cn } from '@/lib/utils'

type Schema = components['schemas']
type DeliveryControl = Schema['DeliveryControlResponse']
type Policy = Schema['DeliveryAdmissionPolicySchema']
type Decision = Schema['DeliveryControlDecisionSchema']
type HoldReason = Schema['DeliveryControlHoldReasonSchema']

function label(value: string): string {
  return value
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function headroom(count: number, limit: number): number {
  return Math.max(0, limit - count)
}

function modeExplanation(mode: Policy['mode']): string {
  if (mode === 'paused') {
    return 'Paused: no new admission occurs. Already-active work is preserved; pausing does not terminate active Symphony sessions.'
  }
  if (mode === 'draining') {
    return 'Draining: no new admission occurs while already-active work is preserved. Draining does not stop or terminate Symphony sessions.'
  }
  return 'Running: new admission may occur only when the server decision and every policy constraint permit it.'
}

function CapacityValue({
  available,
  label: valueLabel,
  limit,
  used,
}: {
  available: number
  label: string
  limit: number
  used: number
}) {
  return (
    <div className='rounded-lg border p-4'>
      <dt className='text-muted-foreground text-sm font-medium'>{valueLabel}</dt>
      <dd className='mt-2 space-y-1'>
        <p className='text-2xl font-semibold tabular-nums'>{used} used</p>
        <p className='text-sm tabular-nums'>
          {available} available before the maximum of {limit}
        </p>
      </dd>
    </div>
  )
}

function PolicyOverview({ policy }: { policy: Policy }) {
  return (
    <Card data-testid='active-policy-card'>
      <CardHeader>
        <CardTitle>Active Atlas delivery policy</CardTitle>
        <CardDescription>
          Operator-owned limits for Atlas admission. Maximums are constraints, never desired utilisation targets.
        </CardDescription>
      </CardHeader>
      <CardContent className='space-y-5'>
        <dl className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
          <div>
            <dt className='text-muted-foreground text-sm'>Policy revision</dt>
            <dd className='mt-1 text-xl font-semibold tabular-nums'>{policy.revision}</dd>
          </div>
          <div>
            <dt className='text-muted-foreground text-sm'>Mode</dt>
            <dd className='mt-1'><Badge variant='outline'>{label(policy.mode)}</Badge></dd>
          </div>
          <div>
            <dt className='text-muted-foreground text-sm'>Approved policy ceiling</dt>
            <dd className='mt-1 text-xl font-semibold tabular-nums'>
              Maximum {policy.approved_symphony_ceiling}
            </dd>
          </div>
          <div>
            <dt className='text-muted-foreground text-sm'>Integration budget</dt>
            <dd className='mt-1 text-xl font-semibold tabular-nums'>
              Maximum {policy.integration_budget}
            </dd>
          </div>
          <div>
            <dt className='text-muted-foreground text-sm'>Revision created</dt>
            <dd className='mt-1 break-words text-sm'>{policy.created_at}</dd>
          </div>
        </dl>
        <Alert>
          <ShieldCheck aria-hidden='true' />
          <AlertTitle>Approved policy ceiling is Atlas policy state</AlertTitle>
          <AlertDescription>
            It bounds Atlas admission and is owned by the operator. It does not report occupied Symphony workers or independently observe live Symphony configuration. The configured Symphony ceiling is governed separately by <code>WORKFLOW.md</code>; a temporary difference is not hidden or interpreted here.
          </AlertDescription>
        </Alert>
        <p className='text-sm'>{modeExplanation(policy.mode)}</p>
      </CardContent>
    </Card>
  )
}

function CapacityOverview({ data }: { data: DeliveryControl }) {
  const { occupancy, policy } = data
  return (
    <Card>
      <CardHeader>
        <CardTitle>Capacity and pressure</CardTitle>
        <CardDescription>
          Materialised Atlas ticket statuses from the server. Working and review pressure remain separate; count-versus-limit headroom does not predict admission.
        </CardDescription>
      </CardHeader>
      <CardContent className='space-y-6'>
        <dl className='grid gap-3 sm:grid-cols-2 lg:grid-cols-3'>
          <CapacityValue
            label='Working capacity'
            used={occupancy.working_occupancy}
            available={occupancy.new_admission_working_capacity}
            limit={policy.working_budget}
          />
          <CapacityValue
            label='Review pressure'
            used={occupancy.review_occupancy}
            available={headroom(occupancy.review_occupancy, policy.review_budget)}
            limit={policy.review_budget}
          />
          <div className='rounded-lg border p-4'>
            <dt className='text-muted-foreground text-sm font-medium'>
              Changes Requested reserve
            </dt>
            <dd className='mt-2 space-y-1'>
              <p className='text-2xl font-semibold tabular-nums'>
                {occupancy.changes_requested_occupancy} used
              </p>
              <p className='text-sm tabular-nums'>
                {occupancy.changes_requested_reserve_remaining} protected capacity remaining of maximum {policy.changes_requested_reserve}
              </p>
            </dd>
          </div>
        </dl>
        <p className='text-muted-foreground text-sm'>
          Changes Requested reserve is protected for rework. It is not ordinary unused capacity for new work. Review headroom is arithmetic over the displayed server count and limit only; the UI never converts working or policy ceiling values into review availability.
        </p>

        <div className='grid gap-6 lg:grid-cols-2'>
          <section aria-labelledby='risk-lane-capacity'>
            <h3 id='risk-lane-capacity' className='font-medium'>Risk lane constraints</h3>
            {occupancy.risk_lane_occupancy.length === 0 ? (
              <p className='text-muted-foreground mt-2 text-sm'>No risk-specific policy limits.</p>
            ) : (
              <dl className='mt-3 space-y-2'>
                {occupancy.risk_lane_occupancy.map((lane) => (
                  <div key={lane.risk_level} className='grid grid-cols-[minmax(0,1fr)_auto] gap-3 rounded-md border p-3 text-sm'>
                    <dt className='break-words'>{label(lane.risk_level)}</dt>
                    <dd className='text-end tabular-nums'>
                      {lane.count} used · {headroom(lane.count, lane.limit)} available · maximum {lane.limit}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
          <section aria-labelledby='component-lane-capacity' className='min-w-0'>
            <h3 id='component-lane-capacity' className='font-medium'>Component lane constraints</h3>
            {occupancy.component_lane_occupancy.length === 0 ? (
              <p className='text-muted-foreground mt-2 text-sm'>No component-specific policy limits.</p>
            ) : (
              <dl className='mt-3 space-y-2'>
                {occupancy.component_lane_occupancy.map((lane) => (
                  <div key={lane.component} className='grid min-w-0 gap-2 rounded-md border p-3 text-sm sm:grid-cols-[minmax(0,1fr)_auto]'>
                    <dt className='break-all font-mono'>{lane.component}</dt>
                    <dd className='text-start tabular-nums sm:text-end'>
                      {lane.count} used · {headroom(lane.count, lane.limit)} available · maximum {lane.limit}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
        </div>

        <details>
          <summary className='cursor-pointer font-medium'>Status occupancy inventory</summary>
          <dl className='mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3'>
            {occupancy.status_occupancy.map((item) => (
              <div key={item.status} className='flex justify-between gap-3 rounded-md border p-2 text-sm'>
                <dt>{label(item.status)}</dt>
                <dd className='tabular-nums'>{item.count}</dd>
              </div>
            ))}
          </dl>
        </details>
      </CardContent>
    </Card>
  )
}

function ReasonDetails({ reason }: { reason: HoldReason }) {
  const details = [
    reason.source_code ? `source ${reason.source_code}` : null,
    reason.selector ? `selector ${reason.selector}` : null,
    reason.observed !== null && reason.observed !== undefined
      ? `observed ${reason.observed}`
      : null,
    reason.limit !== null && reason.limit !== undefined ? `limit ${reason.limit}` : null,
    reason.reserved_capacity !== null && reason.reserved_capacity !== undefined
      ? `protected reserve ${reason.reserved_capacity}`
      : null,
  ].filter((item): item is string => item !== null)
  return (
    <li className='break-words rounded-md border p-2'>
      <code className='font-semibold'>{reason.code}</code>
      {details.length > 0 ? <span>: {details.join(' · ')}</span> : null}
    </li>
  )
}

function DecisionCard({ decision }: { decision: Decision }) {
  const inputs = decision.rank_inputs
  return (
    <article className='min-w-0 rounded-lg border p-4' data-decision={decision.decision}>
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <h3 className='font-semibold'>
          <Link to={ticketDetailHref(decision.ticket_key)} className='hover:underline'>
            {decision.ticket_key}
          </Link>
        </h3>
        <div className='flex items-center gap-2'>
          <Badge variant='outline'>Rank {decision.rank}</Badge>
          <Badge variant={decision.decision === 'admit' ? 'default' : 'secondary'}>
            Server decision: {label(decision.decision)}
          </Badge>
        </div>
      </div>
      <dl className='mt-4 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4'>
        <div><dt className='text-muted-foreground'>Unlock count</dt><dd>{inputs.unlock_count}</dd></div>
        <div><dt className='text-muted-foreground'>Critical path</dt><dd>{inputs.critical_path_member ? `Position ${inputs.critical_path_position}` : 'No'}</dd></div>
        <div><dt className='text-muted-foreground'>Priority</dt><dd>{inputs.priority}</dd></div>
        <div><dt className='text-muted-foreground'>Risk</dt><dd>{inputs.risk_level} · severity {inputs.risk_severity}</dd></div>
        <div className='sm:col-span-2'><dt className='text-muted-foreground'>Continuously eligible since</dt><dd className='break-words'>{inputs.continuously_eligible_since}</dd></div>
        <div className='sm:col-span-2'><dt className='text-muted-foreground'>Eligible age (server microseconds)</dt><dd>{inputs.continuously_eligible_age_microseconds}</dd></div>
      </dl>
      <section aria-label={`Server reasons for ${decision.ticket_key}`} className='mt-4'>
        <h4 className='text-sm font-medium'>Complete server reason set</h4>
        {decision.reasons.length === 0 ? (
          <p className='text-muted-foreground mt-1 text-sm'>No hold reasons returned.</p>
        ) : (
          <ul className='mt-2 space-y-2 text-sm'>
            {decision.reasons.map((reason, index) => (
              <ReasonDetails
                key={`${reason.code}-${reason.source_code ?? ''}-${reason.selector ?? ''}-${index}`}
                reason={reason}
              />
            ))}
          </ul>
        )}
      </section>
    </article>
  )
}

function AdmissionExplanation({ data }: { data: DeliveryControl }) {
  const latest = data.latest_admission
  return (
    <Card>
      <CardHeader>
        <CardTitle>Deterministic admission explanation</CardTitle>
        <CardDescription>
          Persisted server decisions and rank inputs are rendered unchanged. This UI does not rerank tickets, calculate admission, or replace reason sets.
        </CardDescription>
      </CardHeader>
      <CardContent className='space-y-4'>
        {latest ? (
          <>
            <dl className='grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4'>
              <div><dt className='text-muted-foreground'>Admission run</dt><dd className='break-all font-mono text-xs'>{latest.run_id}</dd></div>
              <div><dt className='text-muted-foreground'>Policy revision used</dt><dd>{latest.policy_revision}</dd></div>
              <div><dt className='text-muted-foreground'>Snapshot observed</dt><dd>{latest.snapshot_observed_at}</dd></div>
              <div><dt className='text-muted-foreground'>Evaluated</dt><dd>{latest.evaluated_at}</dd></div>
              <div><dt className='text-muted-foreground'>Selected ticket</dt><dd>{latest.selected_ticket_key ?? 'None'}</dd></div>
              <div><dt className='text-muted-foreground'>Decision count</dt><dd>{latest.decision_count}</dd></div>
            </dl>
            {latest.decisions_truncated ? (
              <Alert>
                <AlertTriangle aria-hidden='true' />
                <AlertTitle>Decision inventory is bounded</AlertTitle>
                <AlertDescription>
                  The server returned the bounded rank-ordered subset shown here and reports {latest.decision_count} total decisions.
                </AlertDescription>
              </Alert>
            ) : null}
            <div className='space-y-3'>
              {latest.decisions.map((decision) => (
                <DecisionCard key={decision.ticket_key} decision={decision} />
              ))}
              {latest.decisions.length === 0 ? (
                <p className='text-muted-foreground text-sm'>The latest server run returned no candidate decisions.</p>
              ) : null}
            </div>
          </>
        ) : (
          <p className='text-muted-foreground text-sm'>The server reports no admission run yet.</p>
        )}
      </CardContent>
    </Card>
  )
}

function ExceptionalState({ data }: { data: DeliveryControl }) {
  const { occupancy } = data
  if (
    occupancy.over_capacity_reasons.length === 0 &&
    data.indeterminate_reasons.length === 0
  ) {
    return null
  }
  return (
    <section aria-label='Server-reported delivery exceptions' className='grid gap-4 lg:grid-cols-2'>
      {occupancy.over_capacity_reasons.length > 0 ? (
        <Alert variant='destructive'>
          <AlertTitle>Server reports over-capacity state</AlertTitle>
          <AlertDescription>
            <ul className='mt-2 space-y-1'>
              {occupancy.over_capacity_reasons.map((reason, index) => (
                <li key={`${reason.dimension}-${reason.selector ?? ''}-${index}`} className='break-words'>
                  <code>{reason.dimension}</code>
                  {reason.selector ? ` (${reason.selector})` : ''}: {reason.count} used, maximum {reason.limit}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}
      {data.indeterminate_reasons.length > 0 ? (
        <Alert variant='destructive'>
          <AlertTitle>Server reports indeterminate delivery state</AlertTitle>
          <AlertDescription>
            <p>No safe or available result is manufactured while these write fences remain unresolved.</p>
            <ul className='mt-2 space-y-1'>
              {data.indeterminate_reasons.map((reason) => (
                <li key={`${reason.admission_run_id}-${reason.ticket_key}`} className='break-words'>
                  <code>{reason.reason}</code> · {reason.state} · {reason.ticket_key} · policy revision {reason.policy_revision} · observed {reason.observed_at}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}
    </section>
  )
}

function SnapshotFreshness({
  data,
  stale,
}: {
  data: DeliveryControl
  stale: boolean
}) {
  return (
    <Alert
      data-testid='delivery-snapshot-freshness'
      className={cn(stale && 'border-destructive/50')}
      aria-live={stale ? 'assertive' : 'polite'}
    >
      {stale ? <AlertTriangle aria-hidden='true' /> : <CircleGauge aria-hidden='true' />}
      <AlertTitle>{stale ? 'Last truthful server snapshot — stale' : 'Current server snapshot'}</AlertTitle>
      <AlertDescription>
        <p>
          Last successful Linear sync reported by Atlas:{' '}
          <span className='break-words font-mono'>{data.last_linear_sync_at ?? 'none reported'}</span>.
        </p>
        <p>
          {stale
            ? 'Loading or recovery retains this snapshot visibly as stale until a successful server replacement arrives.'
            : 'The displayed policy, occupancy, decisions, reasons, and timestamps came from the authenticated delivery-control response.'}
        </p>
      </AlertDescription>
    </Alert>
  )
}

export function DeliveryControlView() {
  const session = useOperatorSession()
  const query = useDeliveryControlQuery(session.authenticated)
  const expiring = useRef(false)

  useEffect(() => {
    if (
      session.authenticated &&
      query.error instanceof AtlasRequestError &&
      query.error.status === 401 &&
      !expiring.current
    ) {
      expiring.current = true
      void session.expireSession().finally(() => {
        expiring.current = false
      })
    }
  }, [query.error, session])

  const data = query.data
  const snapshotStale = query.isFetching || query.isError || !session.authenticated

  return (
    <Main className='space-y-6' fluid>
      <header className='flex flex-wrap items-start justify-between gap-4'>
        <div>
          <p className='text-muted-foreground text-sm font-medium'>Phase 15 instrument</p>
          <h1 className='mt-1 text-2xl font-bold tracking-tight'>Delivery control</h1>
          <p className='text-muted-foreground mt-2 max-w-4xl text-sm'>
            Inspect server-owned Atlas policy, capacity, admission decisions, and explanations. Ceilings and budgets are maximum limits, never targets. This surface has no ticket-state, dispatch, worker, Symphony configuration, WORKFLOW.md, merge, rebase, optimiser, or automatic 1 → 3 → 5 → 7 → 10 ramp controls.
          </p>
        </div>
        <Button
          type='button'
          variant='outline'
          disabled={!session.authenticated || query.isFetching}
          onClick={() => void query.refetch()}
        >
          <RefreshCw aria-hidden='true' className={cn(query.isFetching && 'animate-spin')} />
          {query.isFetching ? 'Refreshing…' : 'Refresh server state'}
        </Button>
      </header>

      {!session.authenticated ? (
        <Alert aria-live='polite'>
          <ShieldCheck aria-hidden='true' />
          <AlertTitle>Operator session required</AlertTitle>
          <AlertDescription>
            <p>The delivery-control read and complete policy command require the shared loopback operator session.</p>
            <Button type='button' size='sm' className='mt-3' onClick={() => session.beginSessionFlow()}>
              Sign in to delivery control
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}

      {query.isPending && session.authenticated ? (
        <LoadingState label='Loading delivery-control state' />
      ) : null}

      {query.isError && !data && session.authenticated ? (
        <RequestErrorState error={query.error} title='Delivery-control request failed' />
      ) : null}

      {data ? (
        <>
          <SnapshotFreshness data={data} stale={snapshotStale} />
          <ExceptionalState data={data} />
          <PolicyOverview policy={data.policy} />
          <CapacityOverview data={data} />
          <AdmissionExplanation data={data} />
          <Card>
            <CardContent>
              <PolicyEditor
                policy={data.policy}
                refreshCurrent={async () => query.refetch()}
              />
            </CardContent>
          </Card>
        </>
      ) : null}
    </Main>
  )
}
