import {
  AlertTriangle,
  CheckCircle2,
  CircleDotDashed,
  GitBranch,
  ShieldAlert,
} from 'lucide-react'
import { Link } from '@tanstack/react-router'
import type { components } from '@/api/atlas-openapi'
import { ticketDetailHref } from '@/app-shell/surfaces'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { cn } from '@/lib/utils'

type Schema = components['schemas']
type DeliveryControl = Schema['DeliveryControlResponse']
type CIPendingTicket = Schema['DeliveryControlCIPendingTicketSchema']
type ExactBaseStatus = Schema['DeliveryControlExactBaseStatus']
type SnapshotStatus = Schema['DeliveryControlSnapshotStatus']

export type ProtectedLaneRegistryIdentity = {
  fingerprint: string
  stateFingerprint: string
  version: string
}

function label(value: string): string {
  return value
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function Identity({ value }: { value: string | null | undefined }) {
  return (
    <span className='break-all font-mono text-xs'>
      {value ?? 'Not recorded by the server'}
    </span>
  )
}

function TypedReasonList({
  empty,
  reasons,
}: {
  empty: string
  reasons: readonly string[]
}) {
  return reasons.length === 0 ? (
    <p className='text-muted-foreground text-sm'>{empty}</p>
  ) : (
    <ul className='min-w-0 space-y-1 text-sm'>
      {reasons.map((reason, index) => (
        <li key={`${reason}-${index}`} className='min-w-0 break-all'>
          <code className='whitespace-normal'>{reason}</code>
        </li>
      ))}
    </ul>
  )
}

function TicketKeys({ keys }: { keys: readonly string[] }) {
  return keys.length === 0 ? (
    <span className='text-muted-foreground'>None</span>
  ) : (
    <ul className='flex flex-wrap gap-x-3 gap-y-1'>
      {keys.map((key) => (
        <li key={key}>
          <Link className='font-mono text-xs hover:underline' to={ticketDetailHref(key)}>
            {key}
          </Link>
        </li>
      ))}
    </ul>
  )
}

function snapshotTitle(status: SnapshotStatus): string {
  if (status === 'coherent') return 'Coherent server snapshot'
  if (status === 'stale') return 'Stale server snapshot'
  return 'Indeterminate server snapshot'
}

export function ServerSnapshotStatus({
  data,
  transportStale,
}: {
  data: DeliveryControl
  transportStale: boolean
}) {
  const { snapshot } = data
  const exceptional = snapshot.status !== 'coherent' || transportStale
  return (
    <Card
      data-testid='delivery-snapshot-freshness'
      className={cn(exceptional && 'border-destructive/50')}
      aria-live={exceptional ? 'assertive' : 'polite'}
    >
      <CardHeader>
        <div className='flex flex-wrap items-center gap-2'>
          {snapshot.status === 'coherent' && !transportStale ? (
            <CheckCircle2 aria-hidden='true' className='size-5' />
          ) : (
            <AlertTriangle aria-hidden='true' className='text-destructive size-5' />
          )}
          <CardTitle>
            {transportStale ? 'Last truthful server response — refresh stale' : snapshotTitle(snapshot.status)}
          </CardTitle>
          <Badge variant={snapshot.status === 'coherent' ? 'outline' : 'destructive'}>
            Server class: {label(snapshot.status)}
          </Badge>
        </div>
        <CardDescription>
          Snapshot freshness and capacity availability are server classifications. A browser refresh failure keeps the last response visibly stale without replacing its stored classification.
        </CardDescription>
      </CardHeader>
      <CardContent className='space-y-5'>
        <div className='grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4'>
          <div>
            <p className='text-muted-foreground'>Last successful Linear sync</p>
            <Identity value={data.last_linear_sync_at} />
          </div>
          <div>
            <p className='text-muted-foreground'>Composite snapshot</p>
            <Identity value={snapshot.fingerprint} />
          </div>
          <div>
            <p className='text-muted-foreground'>Policy revision</p>
            <p className='tabular-nums'>{snapshot.policy_revision}</p>
          </div>
          <div>
            <p className='text-muted-foreground'>Board observed</p>
            <Identity value={snapshot.board.observed_at} />
          </div>
        </div>

        <section aria-label='Complete server snapshot reasons'>
          <h3 className='mb-2 text-sm font-medium'>Complete snapshot reason set</h3>
          <TypedReasonList
            reasons={snapshot.reasons}
            empty='No snapshot reasons returned.'
          />
        </section>

        <details>
          <summary className='cursor-pointer font-medium'>Pinned snapshot identities</summary>
          <dl className='mt-3 grid min-w-0 gap-4 text-sm sm:grid-cols-2 lg:grid-cols-3'>
            <div>
              <dt className='text-muted-foreground'>Last-good board receipt</dt>
              <dd><Identity value={snapshot.board.receipt_id} /></dd>
            </div>
            <div>
              <dt className='text-muted-foreground'>Latest board attempt</dt>
              <dd className='break-words'>
                {snapshot.board.latest_attempt_result ?? 'Not recorded'} ·{' '}
                <Identity value={snapshot.board.latest_attempt_finished_at} />
              </dd>
            </div>
            <div>
              <dt className='text-muted-foreground'>Evidence fingerprint</dt>
              <dd><Identity value={snapshot.evidence.fingerprint} /></dd>
            </div>
            <div>
              <dt className='text-muted-foreground'>Integration fingerprint</dt>
              <dd><Identity value={snapshot.integration.fingerprint} /></dd>
            </div>
            <div>
              <dt className='text-muted-foreground'>Validation registry</dt>
              <dd className='break-words'>
                {snapshot.integration.validation_registry_version} ·{' '}
                <Identity value={snapshot.integration.validation_registry_fingerprint} />
              </dd>
            </div>
            <div>
              <dt className='text-muted-foreground'>Protected-lane state</dt>
              <dd><Identity value={snapshot.integration.protected_lane_state_fingerprint} /></dd>
            </div>
          </dl>
          <section aria-label='Board projection reasons' className='mt-4'>
            <h3 className='mb-2 text-sm font-medium'>Board projection reasons</h3>
            <TypedReasonList reasons={snapshot.board.reasons} empty='No board reasons returned.' />
          </section>
        </details>
      </CardContent>
    </Card>
  )
}

function exactBasePresentation(status: ExactBaseStatus): {
  description: string
  title: string
  variant: 'default' | 'destructive' | 'outline' | 'secondary'
} {
  if (status === 'exact_branch') {
    return {
      description: 'The stored assessment matched this exact contributor head and current base when observed.',
      title: 'Exact branch',
      variant: 'default',
    }
  }
  if (status === 'rebase_required') {
    return {
      description: 'The stored assessment requires the separate operator-owned rebase lane. This console cannot perform it.',
      title: 'Rebase required',
      variant: 'destructive',
    }
  }
  if (status === 'stale') {
    return {
      description: 'The stored assessment no longer pins the current candidate identity.',
      title: 'Stale assessment',
      variant: 'secondary',
    }
  }
  return {
    description: 'The server cannot make an exact-base claim from current stored evidence.',
    title: 'Indeterminate assessment',
    variant: 'outline',
  }
}

function RequiredChecks({ ticket }: { ticket: CIPendingTicket }) {
  const checks = ticket.outcome.check_results
  return (
    <section aria-label={`Required-check states for ${ticket.ticket_key}`}>
      <h4 className='text-sm font-medium'>Required-check states</h4>
      {checks.length === 0 ? (
        <p className='text-muted-foreground mt-2 text-sm'>No bounded check results returned.</p>
      ) : (
        <ul className='mt-2 space-y-2'>
          {checks.map((check, index) => (
            <li
              key={`${check.check_type}-${index}`}
              className='grid min-w-0 gap-2 rounded-md border p-3 text-sm sm:grid-cols-[minmax(0,1fr)_auto]'
            >
              <div className='min-w-0'>
                <p className='font-medium'>{label(check.check_type)}</p>
                <p className='break-words'>
                  Status <code>{check.status}</code> · classification{' '}
                  <code>{check.classification}</code> · {check.evidence_count} evidence identities
                </p>
                {check.evidence_ids.length > 0 ? (
                  <ul className='mt-1 space-y-1'>
                    {check.evidence_ids.map((identity) => (
                      <li key={identity}><Identity value={identity} /></li>
                    ))}
                  </ul>
                ) : null}
              </div>
              {check.evidence_ids_truncated ? (
                <Badge variant='secondary'>Evidence IDs bounded</Badge>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function ValidationPlan({ ticket }: { ticket: CIPendingTicket }) {
  const plan = ticket.validation_plan
  return (
    <section aria-label={`Validation plan for ${ticket.ticket_key}`} className='rounded-lg border p-4'>
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <h4 className='font-medium'>Validation provenance</h4>
        <Badge variant={plan.status === 'available' ? 'default' : 'destructive'}>
          {label(plan.status)}
        </Badge>
      </div>
      <dl className='mt-3 grid min-w-0 gap-3 text-sm sm:grid-cols-2'>
        <div><dt className='text-muted-foreground'>Profiles</dt><dd className='break-words'>{plan.profiles.length > 0 ? plan.profiles.join(', ') : 'None returned'}</dd></div>
        <div><dt className='text-muted-foreground'>Registry</dt><dd className='break-words'>{plan.registry_version}</dd></div>
        <div><dt className='text-muted-foreground'>Base SHA</dt><dd><Identity value={plan.base_sha} /></dd></div>
        <div><dt className='text-muted-foreground'>Head SHA</dt><dd><Identity value={plan.head_sha} /></dd></div>
        <div><dt className='text-muted-foreground'>Plan fingerprint</dt><dd><Identity value={plan.plan_fingerprint} /></dd></div>
        <div><dt className='text-muted-foreground'>Registry fingerprint</dt><dd><Identity value={plan.registry_fingerprint} /></dd></div>
      </dl>
      <div className='mt-3'>
        <p className='mb-1 text-sm font-medium'>Complete validation reasons</p>
        <TypedReasonList reasons={plan.reasons} empty='No validation reasons returned.' />
      </div>
    </section>
  )
}

function ExactBaseAssessment({ ticket }: { ticket: CIPendingTicket }) {
  const assessment = ticket.exact_base
  const presentation = exactBasePresentation(assessment.status)
  return (
    <section
      aria-label={`Exact-base assessment for ${ticket.ticket_key}`}
      className='rounded-lg border p-4'
      data-assessment={assessment.status}
    >
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <h4 className='font-medium'>Exact-base assessment</h4>
        <Badge variant={presentation.variant}>{presentation.title}</Badge>
      </div>
      <p className='text-muted-foreground mt-2 text-sm'>{presentation.description}</p>
      <p className='mt-2 text-sm font-medium'>Assessment evidence only — never merge approval.</p>
      <dl className='mt-3 grid min-w-0 gap-3 text-sm sm:grid-cols-2'>
        <div><dt className='text-muted-foreground'>Assessment ID</dt><dd><Identity value={assessment.assessment_id} /></dd></div>
        <div><dt className='text-muted-foreground'>Observed</dt><dd><Identity value={assessment.observed_at} /></dd></div>
        <div><dt className='text-muted-foreground'>Head SHA</dt><dd><Identity value={assessment.head_sha} /></dd></div>
        <div><dt className='text-muted-foreground'>Base SHA</dt><dd><Identity value={assessment.base_sha} /></dd></div>
      </dl>
      <div className='mt-3'>
        <p className='mb-1 text-sm font-medium'>Complete assessment reasons</p>
        <TypedReasonList reasons={assessment.reasons} empty='No assessment reasons returned.' />
      </div>
    </section>
  )
}

function CIPendingCard({ ticket }: { ticket: CIPendingTicket }) {
  const failure = ticket.outcome.classification === 'implementation_failure'
  const passed = ticket.outcome.classification === 'passed'
  return (
    <article className='min-w-0 space-y-5 rounded-xl border p-4' data-ci-classification={ticket.outcome.classification}>
      <div className='flex flex-wrap items-start justify-between gap-3'>
        <div className='min-w-0'>
          <h3 className='font-semibold'>
            <Link to={ticketDetailHref(ticket.ticket_key)} className='hover:underline'>
              {ticket.ticket_key}
            </Link>
          </h3>
          <p className='text-muted-foreground break-words text-sm'>
            {ticket.repository_owner && ticket.repository_name
              ? `${ticket.repository_owner}/${ticket.repository_name}`
              : 'Repository not recorded'}
            {' · '}
            {ticket.pr_number ? `PR #${ticket.pr_number}` : 'PR not recorded'}
          </p>
        </div>
        <div className='flex flex-wrap gap-2'>
          <Badge variant={failure ? 'destructive' : passed ? 'default' : 'secondary'}>
            CI: {label(ticket.outcome.classification)}
          </Badge>
          <Badge variant='outline'>Decision: {label(ticket.outcome.decision)}</Badge>
        </div>
      </div>

      <dl className='grid min-w-0 gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4'>
        <div className='sm:col-span-2'><dt className='text-muted-foreground'>Exact contributor head</dt><dd><Identity value={ticket.head_sha} /></dd></div>
        <div><dt className='text-muted-foreground'>Outcome reason</dt><dd className='break-all'><code className='whitespace-normal'>{ticket.outcome.reason ?? 'None returned'}</code></dd></div>
        <div><dt className='text-muted-foreground'>Observed</dt><dd><Identity value={ticket.outcome.observed_at} /></dd></div>
        <div className='sm:col-span-2'><dt className='text-muted-foreground'>Reconciliation ID</dt><dd><Identity value={ticket.outcome.reconciliation_id} /></dd></div>
      </dl>

      <section aria-label={`CI projection reasons for ${ticket.ticket_key}`}>
        <h4 className='mb-2 text-sm font-medium'>Complete CI wait/failure reason set</h4>
        <TypedReasonList
          reasons={ticket.outcome.projection_reasons}
          empty='No additional CI projection reasons returned.'
        />
      </section>

      <RequiredChecks ticket={ticket} />
      <div className='grid min-w-0 gap-4 xl:grid-cols-2'>
        <ValidationPlan ticket={ticket} />
        <ExactBaseAssessment ticket={ticket} />
      </div>
    </article>
  )
}

export function CIPendingConsole({ data }: { data: DeliveryControl }) {
  return (
    <Card data-testid='ci-integration-console'>
      <CardHeader>
        <div className='flex flex-wrap items-center gap-2'>
          <CircleDotDashed aria-hidden='true' className='size-5' />
          <CardTitle>CI pending evidence and integration readiness</CardTitle>
          <Badge variant='outline'>{data.ci_pending_ticket_count} server-counted</Badge>
        </div>
        <CardDescription>
          Exact stored heads, validation provenance, check states, and typed server reasons only. Raw CI logs are not requested or rendered, and no result here authorises merge, update, retry, cancellation, transition, or rebase.
        </CardDescription>
      </CardHeader>
      <CardContent className='space-y-4'>
        <Alert>
          <GitBranch aria-hidden='true' />
          <AlertTitle>Exact integration candidate is not an available server class</AlertTitle>
          <AlertDescription>
            The governed feasibility work did not establish a system-tier no-rewrite attestation. This UI displays exact branch, rebase required, stale, and indeterminate assessments distinctly and never manufactures an exact-integration-candidate or merge-ready claim.
          </AlertDescription>
        </Alert>
        {data.ci_pending_tickets_truncated ? (
          <Alert variant='destructive'>
            <AlertTriangle aria-hidden='true' />
            <AlertTitle>CI-pending inventory is bounded</AlertTitle>
            <AlertDescription>
              The server reports {data.ci_pending_ticket_count} candidates; only its bounded response is shown.
            </AlertDescription>
          </Alert>
        ) : null}
        {data.ci_pending_tickets.length === 0 ? (
          <p className='text-muted-foreground text-sm'>The server reports no CI-pending candidates.</p>
        ) : (
          <div className='space-y-4'>
            {data.ci_pending_tickets.map((ticket) => (
              <CIPendingCard key={ticket.ticket_key} ticket={ticket} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function ProtectedLaneConsole({ data }: { data: DeliveryControl }) {
  const occupancy = data.occupancy
  return (
    <Card data-testid='protected-integration-lanes'>
      <CardHeader>
        <div className='flex flex-wrap items-center gap-2'>
          <ShieldAlert aria-hidden='true' className='size-5' />
          <CardTitle>Protected integration lanes</CardTitle>
        </div>
        <CardDescription>
          Current working and CI-pending owners, immutable registry limits, and persisted held candidates. A free Symphony slot never overrides a saturated protected lane.
        </CardDescription>
      </CardHeader>
      <CardContent className='space-y-6'>
        <dl className='grid min-w-0 gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3'>
          <div><dt className='text-muted-foreground'>Registry version</dt><dd className='break-words font-mono text-xs'>{occupancy.protected_lane_registry_version}</dd></div>
          <div><dt className='text-muted-foreground'>Registry fingerprint</dt><dd><Identity value={occupancy.protected_lane_registry_fingerprint} /></dd></div>
          <div><dt className='text-muted-foreground'>Active-state fingerprint</dt><dd><Identity value={occupancy.protected_lane_state_fingerprint} /></dd></div>
        </dl>

        <section aria-labelledby='protected-lane-occupancy-title'>
          <h3 id='protected-lane-occupancy-title' className='font-medium'>Current lane occupants</h3>
          {occupancy.protected_lane_occupancy.length === 0 ? (
            <p className='text-muted-foreground mt-2 text-sm'>No protected lanes returned.</p>
          ) : (
            <div className='mt-3 grid min-w-0 gap-3 lg:grid-cols-2'>
              {occupancy.protected_lane_occupancy.map((lane) => (
                <article key={lane.lane} className='min-w-0 rounded-lg border p-4'>
                  <div className='flex flex-wrap items-center justify-between gap-2'>
                    <h4 className='break-all font-mono text-sm font-semibold'>{lane.lane}</h4>
                    <Badge variant={lane.count >= lane.limit ? 'destructive' : 'outline'}>
                      {lane.count} used · maximum {lane.limit}
                    </Badge>
                  </div>
                  <p className='text-muted-foreground mt-2 text-sm'>
                    {lane.operator_declared ? 'Operator-declared protected hotspot' : 'Repository-declared protected surface'}
                  </p>
                  <div className='mt-3 text-sm'>
                    <p className='mb-1 font-medium'>Current owners</p>
                    <TicketKeys keys={lane.ticket_keys} />
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>

        <section aria-labelledby='protected-lane-holds-title'>
          <h3 id='protected-lane-holds-title' className='font-medium'>Held candidates and complete lane reasons</h3>
          {data.protected_lane_holds.length === 0 ? (
            <p className='text-muted-foreground mt-2 text-sm'>No persisted protected-lane holds returned.</p>
          ) : (
            <ul className='mt-3 space-y-3'>
              {data.protected_lane_holds.map((hold, index) => (
                <li key={`${hold.ticket_key}-${hold.lane}-${index}`} className='min-w-0 rounded-lg border p-4'>
                  <div className='flex flex-wrap items-center gap-2'>
                    <Link className='font-semibold hover:underline' to={ticketDetailHref(hold.ticket_key)}>{hold.ticket_key}</Link>
                    <Badge variant='destructive'>Held by protected lane</Badge>
                  </div>
                  <dl className='mt-3 grid min-w-0 gap-3 text-sm sm:grid-cols-2'>
                    <div><dt className='text-muted-foreground'>Lane</dt><dd className='break-all font-mono text-xs'>{hold.lane}</dd></div>
                    <div><dt className='text-muted-foreground'>Observed / maximum</dt><dd className='tabular-nums'>{hold.observed ?? 'Not recorded'} / {hold.limit ?? 'Not recorded'}</dd></div>
                    <div className='sm:col-span-2'><dt className='text-muted-foreground'>Current owners</dt><dd><TicketKeys keys={hold.owner_ticket_keys} /></dd></div>
                  </dl>
                </li>
              ))}
            </ul>
          )}
        </section>
      </CardContent>
    </Card>
  )
}
