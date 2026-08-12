import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useParams } from '@tanstack/react-router'
import {
  AlertCircle,
  CheckCircle2,
  CircleDashed,
  Clock3,
  RefreshCw,
  ShieldAlert,
  XCircle,
} from 'lucide-react'
import { ATLAS_ACCEPTANCE_REPOSITORY } from '@/api/config'
import type { components } from '@/api/atlas-openapi'
import { atlasOpenApiEnums } from '@/api/atlas-openapi-runtime'
import {
  AtlasRequestError,
  isApiUnreachableError,
  type AtlasAcceptanceConfirmationRequest,
  type AtlasCreateAcceptanceSessionRequest,
} from '@/api/client'
import {
  useAcceptanceSessionQuery,
  useConfirmAcceptanceSessionMutation,
  useCreateAcceptanceSessionMutation,
  usePullAcceptanceEvidenceMutation,
  useReviewsQuery,
  useTicketDetailQuery,
  useVerifyAcceptanceSessionMutation,
} from '@/api/query-hooks'
import { Main } from '@/components/layout/main'
import { LoadingState, RequestErrorState } from '@/components/states'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useOperatorSession } from '@/context/operator-session-provider'
import { cn } from '@/lib/utils'
import { ReviewChecksMatrix } from './review-queue-view'

type Schema = components['schemas']
type AcceptanceSession = Schema['AcceptanceSessionSchema']
type AcceptanceStep = Schema['AcceptanceSessionStep']
type BlockingReason = Schema['AcceptanceSessionBlockingReason']
type ActionReceipt = Schema['AcceptanceActionReceiptSchema']
type CreationReceipt = Schema['AcceptanceCreationReceiptSchema']
type NextAction = 'confirm' | 'evidence' | 'verify'

type CreateCommand = {
  action: 'create'
  idempotencyKey: string
  prNumber: number
  request: AtlasCreateAcceptanceSessionRequest
}
type StepCommand =
  | {
      action: 'confirm'
      idempotencyKey: string
      request: AtlasAcceptanceConfirmationRequest
      sessionId: string
    }
  | {
      action: 'evidence' | 'verify'
      idempotencyKey: string
      request: Record<string, never>
      sessionId: string
    }
type PendingCommand = CreateCommand | StepCommand

type ErrorKind =
  | 'blocked'
  | 'external-read'
  | 'failed'
  | 'replay-conflict'
  | 'security'
  | 'session-expired'
  | 'stale'
  | 'timeout'

type PanelError = {
  detail: string
  kind: ErrorKind
  reasons: BlockingReason[]
  recoveryCommand?: string | null
  title: string
  validationErrors: string[]
}

const stepLabels = {
  confirmations: 'Confirm criteria',
  evidence: 'Pull exact-head evidence',
  preflight: 'Exact-head preflight',
  readiness: 'Live merge readiness',
  verification: 'Run canonical verification',
} satisfies Record<AcceptanceStep, string>

function labelFromValue(value: string): string {
  return value
    .split('_')
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(' ')
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return 'Not recorded'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function isErrorBody(
  body: unknown
): body is Schema['AcceptanceSessionErrorResponse'] {
  return (
    typeof body === 'object' &&
    body !== null &&
    typeof (body as { detail?: unknown }).detail === 'string'
  )
}

function panelError(error: unknown): PanelError {
  if (isApiUnreachableError(error)) {
    return {
      detail:
        'Atlas did not return an unambiguous command result. The command key is retained for an explicit safe retry.',
      kind: 'external-read',
      reasons: [],
      title: 'Atlas API unreachable',
      validationErrors: [],
    }
  }

  if (!(error instanceof AtlasRequestError)) {
    return {
      detail: 'The acceptance action failed before Atlas returned a typed result.',
      kind: 'failed',
      reasons: [],
      title: 'Acceptance action failed',
      validationErrors: [],
    }
  }

  const body = isErrorBody(error.body) ? error.body : null
  const reasons = body?.reasons ?? []
  const base = {
    detail: body?.detail ?? `Atlas returned HTTP ${error.status}.`,
    reasons,
    recoveryCommand: body?.recovery_command,
    validationErrors: body?.validation_errors ?? [],
  }

  if (error.status === 401 || error.status === 404) {
    return {
      ...base,
      kind: 'session-expired',
      title:
        error.status === 401
          ? 'Operator session expired'
          : 'Acceptance session expired or unavailable',
    }
  }
  if (error.status === 403) {
    return { ...base, kind: 'security', title: 'Security refusal' }
  }
  if (
    error.status === 504 ||
    body?.result_code === 'external_timeout' ||
    reasons.includes('external_read_timeout')
  ) {
    return { ...base, kind: 'timeout', title: 'Acceptance action timed out' }
  }
  if (error.status === 409) {
    const stale =
      body?.result_code === 'stale_state' ||
      reasons.includes('session_stale') ||
      reasons.some((reason) => reason.endsWith('_mismatch'))
    return stale
      ? { ...base, kind: 'stale', title: 'Exact-head session is stale' }
      : {
          ...base,
          kind: 'replay-conflict',
          title: body?.conflict_code
            ? `Command conflict: ${labelFromValue(body.conflict_code)}`
            : 'Acceptance command conflict',
        }
  }
  if (error.status === 422) {
    return { ...base, kind: 'blocked', title: 'Acceptance action blocked' }
  }
  if (error.status === 502 || error.status === 503) {
    return { ...base, kind: 'external-read', title: 'External assessment failed' }
  }
  return { ...base, kind: 'failed', title: 'Acceptance action failed' }
}

function nextAction(lifecycle: AcceptanceSession['lifecycle']): NextAction | null {
  if (lifecycle === 'preflight_passed') return 'evidence'
  if (lifecycle === 'evidence_ready') return 'confirm'
  if (lifecycle === 'confirmations_ready') return 'verify'
  return null
}

function currentStep(action: NextAction | null): AcceptanceStep | undefined {
  if (action === 'evidence') return 'evidence'
  if (action === 'confirm') return 'confirmations'
  if (action === 'verify') return 'verification'
  return undefined
}

function Detail({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div className='min-w-0'>
      <dt className='text-muted-foreground text-xs font-medium uppercase'>
        {label}
      </dt>
      <dd className='mt-1 text-sm'>{children}</dd>
    </div>
  )
}

function Sha({ children }: { children: string }) {
  return <span className='font-mono text-xs break-all'>{children}</span>
}

function ServerReasons({
  reasons,
  title = 'Server blocking reasons',
}: {
  reasons: readonly BlockingReason[]
  title?: string
}) {
  if (reasons.length === 0) return null
  return (
    <section aria-label={title} className='space-y-2'>
      <h4 className='text-sm font-medium'>{title}</h4>
      <ul className='grid gap-2'>
        {reasons.map((reason, index) => (
          <li
            className='border-border bg-muted/30 rounded-md border px-3 py-2 text-sm'
            key={`${reason}-${index}`}
          >
            <span>{labelFromValue(reason)}</span>{' '}
            <code className='text-muted-foreground break-all'>({reason})</code>
          </li>
        ))}
      </ul>
    </section>
  )
}

const newSessionReasons = new Set<BlockingReason>([
  'base_ref_mismatch',
  'base_repository_mismatch',
  'base_sha_mismatch',
  'close_set_mismatch',
  'criteria_mismatch',
  'head_ref_mismatch',
  'head_repository_mismatch',
  'head_sha_mismatch',
  'repository_mismatch',
  'session_stale',
])

const phase12RecoveryReasons = new Set<BlockingReason>([
  'integration_behind',
  'integration_conflicted',
  'integration_diverged',
])

function hasReason(
  reasons: readonly BlockingReason[],
  candidates: ReadonlySet<BlockingReason>
): boolean {
  return reasons.some((reason) => candidates.has(reason))
}

function RecoveryCopy({ error }: { error: PanelError }) {
  const integrationRecovery = error.reasons.some((reason) =>
    ['integration_behind', 'integration_conflicted', 'integration_diverged'].includes(
      reason
    )
  )
  const movement = error.reasons.some(
    (reason) => reason.endsWith('_mismatch') || reason === 'criteria_mismatch'
  )

  if (!integrationRecovery && !movement && !error.recoveryCommand) return null
  return (
    <div className='mt-2 space-y-2'>
      {integrationRecovery ? (
        <p>
          This is Phase 12 mechanical-staleness recovery. Refresh the live state,
          then use the operator-owned rebase lane outside this UI before starting a
          new exact-head session.
        </p>
      ) : null}
      {movement ? (
        <p>
          Head, main, repository, close-set, or criteria movement requires a fresh
          GET and a new acceptance session. This session remains inspectable history.
        </p>
      ) : null}
      {error.recoveryCommand ? (
        <p>
          Server recovery command:{' '}
          <code className='bg-muted rounded px-1 py-0.5 break-all'>
            {error.recoveryCommand}
          </code>
        </p>
      ) : null}
    </div>
  )
}

function ErrorNotice({
  error,
  pendingCommand,
  busy,
  onRefresh,
  onRetry,
}: {
  error: PanelError
  pendingCommand: PendingCommand | null
  busy: boolean
  onRefresh: () => void
  onRetry: (command: PendingCommand) => void
}) {
  const Icon = error.kind === 'security' ? ShieldAlert : AlertCircle
  return (
    <Alert
      ref={(node) => node?.setAttribute('tabindex', '-1')}
      data-error-kind={error.kind}
      variant='destructive'
      aria-live='assertive'
    >
      <Icon aria-hidden='true' />
      <AlertTitle>{error.title}</AlertTitle>
      <AlertDescription>
        <p>{error.detail}</p>
        <ServerReasons reasons={error.reasons} />
        {error.validationErrors.length > 0 ? (
          <ul className='list-disc pl-5'>
            {error.validationErrors.map((item) => (
              <li key={item}>{labelFromValue(item)}</li>
            ))}
          </ul>
        ) : null}
        <RecoveryCopy error={error} />
        <div className='mt-3 flex flex-wrap gap-2'>
          {pendingCommand ? (
            <Button
              type='button'
              size='sm'
              disabled={busy}
              onClick={() => onRetry(pendingCommand)}
            >
              Retry same command key
            </Button>
          ) : null}
          {['external-read', 'stale', 'timeout'].includes(error.kind) ? (
            <Button
              type='button'
              size='sm'
              variant='outline'
              disabled={busy}
              onClick={onRefresh}
            >
              {error.kind === 'stale'
                ? 'Refresh stale session'
                : 'Refresh before new command'}
            </Button>
          ) : null}
        </div>
      </AlertDescription>
    </Alert>
  )
}

function ReceiptCard({ receipt }: { receipt: ActionReceipt }) {
  return (
    <section
      aria-label='Latest acceptance action receipt'
      className='border-border rounded-lg border p-4'
    >
      <h3 className='font-medium'>Latest server receipt</h3>
      <dl className='mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3'>
        <Detail label='Action'>{receipt.action}</Detail>
        <Detail label='Outcome'>{labelFromValue(receipt.outcome)}</Detail>
        <Detail label='Result'>{labelFromValue(receipt.result_code)}</Detail>
        <Detail label='Receipt ID'>{receipt.receipt_id}</Detail>
        <Detail label='Created'>{formatTimestamp(receipt.created_at)}</Detail>
        <Detail label='Completed'>{formatTimestamp(receipt.completed_at)}</Detail>
      </dl>
    </section>
  )
}

function CreationReceiptCard({ receipt }: { receipt: CreationReceipt }) {
  return (
    <section
      aria-label='Acceptance session creation receipt'
      className='border-border rounded-lg border p-4'
    >
      <h3 className='font-medium'>Session creation receipt</h3>
      <dl className='mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3'>
        <Detail label='Action'>{receipt.action}</Detail>
        <Detail label='Outcome'>{labelFromValue(receipt.outcome)}</Detail>
        <Detail label='Completed'>{formatTimestamp(receipt.completed_at)}</Detail>
      </dl>
    </section>
  )
}

function SessionIdentity({ session }: { session: AcceptanceSession }) {
  const { pinned_identity: identity } = session
  return (
    <Card>
      <CardHeader>
        <CardTitle className='flex flex-wrap items-center justify-between gap-3'>
          <span>Exact-head identity</span>
          <Badge variant='outline'>{labelFromValue(session.lifecycle)}</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className='space-y-5'>
        <dl className='grid gap-4 sm:grid-cols-2 lg:grid-cols-3'>
          <Detail label='Repository'>
            {identity.repository.owner}/{identity.repository.name}
          </Detail>
          <Detail label='Pull request'>#{identity.pr_number}</Detail>
          <Detail label='Session ID'>{session.session_id}</Detail>
          <Detail label='Head ref'>{identity.head.ref}</Detail>
          <Detail label='Head repository'>{identity.head.repository}</Detail>
          <Detail label='Head SHA'>
            <Sha>{identity.head.sha}</Sha>
          </Detail>
          <Detail label='Base ref'>{identity.base.ref}</Detail>
          <Detail label='Base repository'>{identity.base.repository}</Detail>
          <Detail label='Base SHA'>
            <Sha>{identity.base.sha}</Sha>
          </Detail>
          <Detail label='Lifecycle'>{labelFromValue(session.lifecycle)}</Detail>
          <Detail label='Created'>
            {formatTimestamp(session.timestamps.created_at)}
          </Detail>
          <Detail label='Updated'>
            {formatTimestamp(session.timestamps.updated_at)}
          </Detail>
          <Detail label='Staled'>
            {formatTimestamp(session.timestamps.staled_at)}
          </Detail>
          <Detail label='Actor'>
            {session.actor.type}/{session.actor.id}
          </Detail>
          <Detail label='Criteria fingerprint'>
            <Sha>{session.criteria_fingerprint}</Sha>
          </Detail>
        </dl>
        <div>
          <h3 className='text-sm font-medium'>Close-set</h3>
          <ul className='mt-2 flex flex-wrap gap-2'>
            {session.close_set.map((key) => (
              <li key={key}>
                <Badge variant='secondary'>{key}</Badge>
              </li>
            ))}
          </ul>
        </div>
        <details>
          <summary className='cursor-pointer text-sm font-medium'>
            Initial exact-head assessment
          </summary>
          <dl className='mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3'>
            {Object.entries(session.initial_assessment).map(([key, value]) => (
              <Detail key={key} label={labelFromValue(key)}>
                {value === null ? 'Not reported' : String(value)}
              </Detail>
            ))}
          </dl>
        </details>
      </CardContent>
    </Card>
  )
}

function StepStateIcon({ state }: { state: Schema['AcceptanceSessionStepState'] }) {
  if (state === 'complete') return <CheckCircle2 aria-hidden='true' className='size-5' />
  if (state === 'pending') return <CircleDashed aria-hidden='true' className='size-5' />
  if (state === 'blocked') return <AlertCircle aria-hidden='true' className='size-5' />
  return <XCircle aria-hidden='true' className='size-5' />
}

function StepTimeline({
  next,
  session,
}: {
  next: NextAction | null
  session: AcceptanceSession
}) {
  const activeStep = currentStep(next)
  return (
    <Card>
      <CardHeader>
        <CardTitle>Governed acceptance steps</CardTitle>
      </CardHeader>
      <CardContent>
        <ol aria-label='Acceptance state machine' className='grid gap-3'>
          {atlasOpenApiEnums.AcceptanceSessionStep.map((step) => {
            const summary = session.steps[step]
            return (
              <li
                aria-current={activeStep === step ? 'step' : undefined}
                className={cn(
                  'border-border rounded-lg border p-4',
                  activeStep === step && 'border-primary ring-primary/20 ring-2'
                )}
                data-step={step}
                data-step-state={summary?.state ?? 'not_reported'}
                key={step}
              >
                <div className='flex items-start gap-3'>
                  {summary ? (
                    <StepStateIcon state={summary.state} />
                  ) : (
                    <CircleDashed aria-hidden='true' className='size-5' />
                  )}
                  <div className='min-w-0 flex-1'>
                    <div className='flex flex-wrap items-center justify-between gap-2'>
                      <h3 className='font-medium'>{stepLabels[step]}</h3>
                      <Badge variant='outline'>
                        {summary ? labelFromValue(summary.state) : 'Not reported'}
                      </Badge>
                    </div>
                    <p className='text-muted-foreground mt-1 text-sm'>
                      {summary?.occurred_at
                        ? formatTimestamp(summary.occurred_at)
                        : 'No completion timestamp recorded.'}
                    </p>
                    {summary?.receipt_ids.length ? (
                      <p className='text-muted-foreground mt-2 text-xs break-all'>
                        Receipt IDs: {summary.receipt_ids.join(', ')}
                      </p>
                    ) : null}
                    {summary ? (
                      <div className='mt-3'>
                        <ServerReasons
                          reasons={summary.reasons}
                          title={`${stepLabels[step]} reasons`}
                        />
                      </div>
                    ) : null}
                  </div>
                </div>
              </li>
            )
          })}
        </ol>
      </CardContent>
    </Card>
  )
}

function EvidenceSummary({ session }: { session: AcceptanceSession }) {
  const evidence = session.steps.evidence?.evidence
  return (
    <Card>
      <CardHeader>
        <CardTitle>Evidence trust and pin state</CardTitle>
      </CardHeader>
      <CardContent>
        {evidence ? (
          <dl className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
            <Detail label='Total'>{evidence.total_count}</Detail>
            <Detail label='New in pull'>{evidence.new_count}</Detail>
            <Detail label='System trust'>{evidence.system_count}</Detail>
            <Detail label='Human trust'>{evidence.human_count}</Detail>
            <Detail label='Agent trust'>{evidence.agent_count}</Detail>
            <Detail label='Passed'>{evidence.passed_count}</Detail>
            <Detail label='Pending'>{evidence.pending_count}</Detail>
            <Detail label='Failed'>{evidence.failed_count}</Detail>
            <Detail label='Warnings'>{evidence.warning_count}</Detail>
            <Detail label='Complete pins'>
              {evidence.complete_pin_count} ({evidence.pin_complete ? 'complete' : 'incomplete'})
            </Detail>
            <Detail label='Exact-head pins'>
              {evidence.exact_head_pin_count} ({evidence.exact_head_pin_complete ? 'complete' : 'incomplete'})
            </Detail>
            <Detail label='Source span'>
              {formatTimestamp(evidence.oldest_source_event_at)} —{' '}
              {formatTimestamp(evidence.latest_source_event_at)}
            </Detail>
          </dl>
        ) : (
          <p className='text-muted-foreground text-sm'>
            The server has not returned an evidence summary for this session. Raw
            evidence payloads are never exposed here.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function CriteriaConfirmation({
  busy,
  checkedIndexes,
  manualApproval,
  next,
  onCheckedChange,
  onManualApprovalChange,
  onSubmit,
  session,
}: {
  busy: boolean
  checkedIndexes: ReadonlySet<number>
  manualApproval: boolean
  next: NextAction | null
  onCheckedChange: (index: number, checked: boolean) => void
  onManualApprovalChange: (checked: boolean) => void
  onSubmit: () => void
  session: AcceptanceSession
}) {
  const completed = session.steps.confirmations?.state === 'complete'
  const enabled = next === 'confirm' && !busy
  const everyChecked = session.criteria_snapshot.every((criterion) =>
    checkedIndexes.has(criterion.criterion_index)
  )
  return (
    <Card>
      <CardHeader>
        <CardTitle>Server-snapshot criteria confirmation</CardTitle>
      </CardHeader>
      <CardContent className='space-y-4'>
        <p className='text-muted-foreground text-sm'>
          Criterion text is inert and server owned. Confirm each stable index, then
          give the separate manual approval. Only indexes, the fingerprint, and the
          approval boolean are submitted.
        </p>
        <fieldset className='grid gap-3' disabled={!enabled}>
          <legend className='sr-only'>Acceptance criteria</legend>
          {session.criteria_snapshot.map((criterion) => {
            const inputId = `criterion-${session.session_id}-${criterion.criterion_index}`
            return (
              <div
                className='border-border grid grid-cols-[auto_minmax(0,1fr)] gap-3 rounded-lg border p-3'
                key={`${criterion.ticket_key}-${criterion.criterion_index}`}
              >
                <Checkbox
                  id={inputId}
                  checked={completed || checkedIndexes.has(criterion.criterion_index)}
                  onCheckedChange={(checked) =>
                    onCheckedChange(criterion.criterion_index, checked === true)
                  }
                />
                <Label htmlFor={inputId} className='min-w-0 items-start leading-relaxed'>
                  <span className='text-muted-foreground mr-2 text-xs'>
                    {criterion.ticket_key} · index {criterion.criterion_index}
                  </span>
                  <span>{criterion.text}</span>
                </Label>
              </div>
            )
          })}
          <div className='border-primary/30 grid grid-cols-[auto_minmax(0,1fr)] gap-3 rounded-lg border p-3'>
            <Checkbox
              id={`manual-approval-${session.session_id}`}
              checked={completed || manualApproval}
              onCheckedChange={(checked) =>
                onManualApprovalChange(checked === true)
              }
            />
            <Label htmlFor={`manual-approval-${session.session_id}`}>
              I give the separate manual approval for this exact pinned head.
            </Label>
          </div>
        </fieldset>
        {next === 'confirm' ? (
          <Button
            type='button'
            disabled={busy || !everyChecked || !manualApproval}
            onClick={onSubmit}
          >
            Confirm every criterion
          </Button>
        ) : null}
      </CardContent>
    </Card>
  )
}

function VerificationSummary({
  reviews,
  session,
}: {
  reviews: readonly Schema['ReviewQueueItemSchema'][]
  session: AcceptanceSession
}) {
  const verification = session.steps.verification?.verification
  return (
    <Card>
      <CardHeader>
        <CardTitle>Canonical verification</CardTitle>
      </CardHeader>
      <CardContent className='space-y-5'>
        <dl className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
          <Detail label='Top-level verdict'>
            {verification ? labelFromValue(verification.status) : 'Not run'}
          </Detail>
          <Detail label='Verified head'>
            {verification?.head_commit ? (
              <Sha>{verification.head_commit}</Sha>
            ) : (
              'Not verified'
            )}
          </Detail>
          <Detail label='Ticket count'>
            {verification?.ticket_count ?? 'Not reported'}
          </Detail>
          <Detail label='Blocking check count'>
            {verification?.blocking_check_count ?? 'Not reported'}
          </Detail>
        </dl>
        <div className='grid gap-4 lg:grid-cols-2'>
          {session.close_set.map((key) => {
            const review = reviews.find((item) => item.key === key)
            return (
              <section aria-label={`Canonical verification matrix for ${key}`} key={key}>
                <h3 className='mb-2 text-sm font-medium'>{key}</h3>
                <ReviewChecksMatrix checks={review?.checks ?? []} />
              </section>
            )
          })}
        </div>
        <ServerReasons reasons={session.steps.verification?.reasons ?? []} />
      </CardContent>
    </Card>
  )
}

function LiveReadiness({
  current,
  reasons,
  session,
}: {
  current: boolean
  reasons: readonly BlockingReason[]
  session: AcceptanceSession
}) {
  const verification = session.steps.verification?.verification
  const verifiedHead = verification?.head_commit

  if (current && verifiedHead) {
    return (
      <Alert data-readiness='merge-ready' aria-live='polite'>
        <CheckCircle2 aria-hidden='true' />
        <AlertTitle>Exact verified head is ready for manual merge</AlertTitle>
        <AlertDescription>
          <p>
            Merge this exact verified SHA manually in GitHub. Atlas does not perform
            the merge.
          </p>
          <Sha>{verifiedHead}</Sha>
        </AlertDescription>
      </Alert>
    )
  }

  return (
    <Alert data-readiness='blocked' variant='destructive' aria-live='polite'>
      <AlertCircle aria-hidden='true' />
      <AlertTitle>Live merge readiness is closed</AlertTitle>
      <AlertDescription>
        <p>
          Only a current successful session GET can open the manual-merge
          instruction. Stored verification history is not current authority.
        </p>
        <ServerReasons reasons={reasons} title='Live readiness reasons' />
        {hasReason(reasons, newSessionReasons) ? (
          <p>
            The server observed identity, head, base, close-set, or criteria
            movement. Refresh, inspect the reasons, and start a new exact-head
            session; never reuse this session’s command key.
          </p>
        ) : null}
        {hasReason(reasons, phase12RecoveryReasons) ? (
          <p>
            Use the operator-owned Phase 12 rebase lane outside this UI, then
            refresh and start a new exact-head session. This panel does not rebase.
          </p>
        ) : null}
      </AlertDescription>
    </Alert>
  )
}

function SessionEntry({
  busy,
  defaultPr,
  loadInput,
  onCreate,
  onLoad,
  onLoadInputChange,
  prInput,
  repository,
  setPrInput,
  setRepository,
}: {
  busy: boolean
  defaultPr: string | null
  loadInput: string
  onCreate: (event: FormEvent<HTMLFormElement>) => void
  onLoad: (event: FormEvent<HTMLFormElement>) => void
  onLoadInputChange: (value: string) => void
  prInput: string
  repository: string
  setPrInput: (value: string) => void
  setRepository: (value: string) => void
}) {
  return (
    <div className='grid gap-4 lg:grid-cols-2'>
      <Card>
        <CardHeader>
          <CardTitle>Create an exact-head session</CardTitle>
        </CardHeader>
        <CardContent>
          <form className='grid gap-4' onSubmit={onCreate}>
            <div className='grid gap-2'>
              <Label htmlFor='acceptance-repository'>Repository</Label>
              <Input
                id='acceptance-repository'
                value={repository}
                placeholder='owner/repository'
                autoCapitalize='none'
                autoComplete='off'
                spellCheck={false}
                disabled={busy}
                required
                onChange={(event) => setRepository(event.target.value)}
              />
              <p className='text-muted-foreground text-xs'>
                This selector is validated against the server allowlist and is never
                used as a browser URL.
              </p>
            </div>
            <div className='grid gap-2'>
              <Label htmlFor='acceptance-pr-number'>Pull request number</Label>
              <Input
                id='acceptance-pr-number'
                value={prInput}
                inputMode='numeric'
                pattern='[0-9]+'
                disabled={busy}
                required
                onChange={(event) => setPrInput(event.target.value)}
              />
              {defaultPr ? (
                <p className='text-muted-foreground text-xs'>
                  Prefilled from the ticket’s stored GitHub identifier: {defaultPr}.
                </p>
              ) : null}
            </div>
            <Button type='submit' disabled={busy}>
              Create exact-head session
            </Button>
          </form>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Load an existing session</CardTitle>
        </CardHeader>
        <CardContent>
          <form className='grid gap-4' onSubmit={onLoad}>
            <div className='grid gap-2'>
              <Label htmlFor='acceptance-session-id'>Session ID</Label>
              <Input
                id='acceptance-session-id'
                value={loadInput}
                autoCapitalize='none'
                autoComplete='off'
                spellCheck={false}
                disabled={busy}
                required
                onChange={(event) => onLoadInputChange(event.target.value)}
              />
            </div>
            <Button type='submit' variant='outline' disabled={busy}>
              Load session with fresh GET
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}

export function AcceptanceSessionPanel({ ticketKey }: { ticketKey: string }) {
  const operatorSession = useOperatorSession()
  const ticketQuery = useTicketDetailQuery(ticketKey)
  const reviewsQuery = useReviewsQuery()
  const [sessionId, setSessionId] = useState<string | null>(null)
  const sessionQuery = useAcceptanceSessionQuery(sessionId)
  const createMutation = useCreateAcceptanceSessionMutation()
  const evidenceMutation = usePullAcceptanceEvidenceMutation()
  const confirmationMutation = useConfirmAcceptanceSessionMutation()
  const verificationMutation = useVerifyAcceptanceSessionMutation()
  const [repository, setRepository] = useState(ATLAS_ACCEPTANCE_REPOSITORY)
  const [prInput, setPrInput] = useState('')
  const [loadInput, setLoadInput] = useState('')
  const [checkedIndexes, setCheckedIndexes] = useState<Set<number>>(new Set())
  const [manualApproval, setManualApproval] = useState(false)
  const [pendingCommand, setPendingCommand] = useState<PendingCommand | null>(null)
  const [lastReceipt, setLastReceipt] = useState<ActionReceipt | null>(null)
  const [creationReceipt, setCreationReceipt] = useState<CreationReceipt | null>(
    null
  )
  const [error, setError] = useState<PanelError | null>(null)
  const [announcement, setAnnouncement] = useState<string | null>(null)
  const [busyAction, setBusyAction] = useState<PendingCommand['action'] | 'refresh' | null>(
    null
  )
  const inFlight = useRef(false)
  const focusAfterRead = useRef(false)
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  const errorRef = useRef<HTMLDivElement | null>(null)
  const handledAuthError = useRef<unknown>(null)

  const defaultPr = useMemo(() => {
    const external = ticketQuery.data?.external_github_issue_id?.trim()
    return external && /^\d+$/.test(external) ? external : null
  }, [ticketQuery.data?.external_github_issue_id])

  useEffect(() => {
    if (defaultPr && prInput === '') setPrInput(defaultPr)
  }, [defaultPr, prInput])

  useEffect(() => {
    setSessionId(null)
    setPendingCommand(null)
    setCheckedIndexes(new Set())
    setManualApproval(false)
    setBusyAction(null)
    inFlight.current = false
  }, [operatorSession.acceptanceSessionRevision])

  useEffect(() => {
    if (error) errorRef.current?.focus()
  }, [error])

  useEffect(() => {
    if (focusAfterRead.current && sessionQuery.isSuccess && !sessionQuery.isFetching) {
      focusAfterRead.current = false
      headingRef.current?.focus()
    }
  }, [sessionQuery.isFetching, sessionQuery.isSuccess, sessionQuery.dataUpdatedAt])

  useEffect(() => {
    if (sessionQuery.error && handledAuthError.current !== sessionQuery.error) {
      handledAuthError.current = sessionQuery.error
      setError(panelError(sessionQuery.error))
      if (
        sessionQuery.error instanceof AtlasRequestError &&
        sessionQuery.error.status === 401
      ) {
        void operatorSession.expireSession()
      }
    }
  }, [operatorSession, sessionQuery.error])

  const busy = busyAction !== null || sessionQuery.isFetching
  const session = sessionQuery.data?.session
  const readIsCurrent =
    sessionQuery.isSuccess && !sessionQuery.isFetching && !sessionQuery.isError
  const permittedAction = session && readIsCurrent ? nextAction(session.lifecycle) : null
  const liveReasons = sessionQuery.data?.reasons ?? session?.blocking_reasons ?? []
  const canStartNewSession =
    session !== undefined &&
    (['blocked', 'failed', 'stale'].includes(session.lifecycle) ||
      hasReason(liveReasons, newSessionReasons) ||
      hasReason(liveReasons, phase12RecoveryReasons))

  async function refreshCurrentState(): Promise<void> {
    if (!sessionId || inFlight.current) return
    inFlight.current = true
    setBusyAction('refresh')
    setError(null)
    setAnnouncement('Refreshing live acceptance state…')
    focusAfterRead.current = true
    try {
      const [result] = await Promise.all([
        sessionQuery.refetch({ cancelRefetch: false }),
        reviewsQuery.refetch(),
      ])
      if (result.error) throw result.error
      setPendingCommand(null)
      setAnnouncement('Live acceptance state refreshed from the server.')
    } catch (refreshError) {
      setError(panelError(refreshError))
      setAnnouncement(null)
    } finally {
      setBusyAction(null)
      inFlight.current = false
    }
  }

  async function refreshAfterCommand(response: {
    receipt: ActionReceipt
  }): Promise<void> {
    setLastReceipt(response.receipt)
    setPendingCommand(null)
    setAnnouncement(
      `Atlas returned ${response.receipt.action} with ${response.receipt.result_code}. Refreshing current live readiness.`
    )
    focusAfterRead.current = true
    const [result] = await Promise.all([
      sessionQuery.refetch({ cancelRefetch: false }),
      reviewsQuery.refetch(),
    ])
    if (result.error) throw result.error
  }

  async function execute(command: PendingCommand): Promise<void> {
    if (inFlight.current) return
    if (!operatorSession.authenticated) {
      operatorSession.beginSessionFlow()
      return
    }
    inFlight.current = true
    setBusyAction(command.action)
    setError(null)
    setAnnouncement(null)
    try {
      if (command.action === 'create') {
        const response = await createMutation.mutateAsync({
          idempotencyKey: command.idempotencyKey,
          prNumber: command.prNumber,
          request: command.request,
        })
        setCreationReceipt(response.receipt)
        setPendingCommand(null)
        setSessionId(response.session.session_id)
        setLoadInput(response.session.session_id)
        focusAfterRead.current = true
        setAnnouncement(
          'Atlas created the immutable exact-head session. Loading a fresh live-readiness GET.'
        )
        return
      }

      if (command.action === 'evidence') {
        const response = await evidenceMutation.mutateAsync(command)
        await refreshAfterCommand(response)
      } else if (command.action === 'confirm') {
        const response = await confirmationMutation.mutateAsync(command)
        await refreshAfterCommand(response)
      } else {
        const response = await verificationMutation.mutateAsync(command)
        await refreshAfterCommand(response)
      }
    } catch (commandError) {
      const display = panelError(commandError)
      const ambiguous = isApiUnreachableError(commandError)
      setPendingCommand(ambiguous ? command : null)
      setError(display)
      setAnnouncement(null)
      if (commandError instanceof AtlasRequestError && commandError.status === 401) {
        await operatorSession.expireSession()
      }
    } finally {
      createMutation.reset()
      evidenceMutation.reset()
      confirmationMutation.reset()
      verificationMutation.reset()
      setBusyAction(null)
      inFlight.current = false
    }
  }

  function createSession(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const prNumber = Number(prInput)
    const slug = repository.trim()
    if (!Number.isSafeInteger(prNumber) || prNumber <= 0 || slug === '') {
      setError({
        detail: 'Enter an owner/repository selector and a positive pull request number.',
        kind: 'blocked',
        reasons: [],
        title: 'Session input is incomplete',
        validationErrors: [],
      })
      return
    }
    void execute({
      action: 'create',
      idempotencyKey: window.crypto.randomUUID(),
      prNumber,
      request: { repository: slug },
    })
  }

  function loadSession(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const id = loadInput.trim()
    if (!operatorSession.authenticated) {
      operatorSession.beginSessionFlow()
      return
    }
    if (!id) return
    setError(null)
    setAnnouncement('Loading a fresh live-readiness GET for the requested session.')
    setSessionId(id)
    focusAfterRead.current = true
  }

  function executeNext(action: NextAction): void {
    if (!sessionId || !session) return
    if (action === 'confirm') {
      const criterionIndexes = session.criteria_snapshot
        .filter((criterion) => checkedIndexes.has(criterion.criterion_index))
        .map((criterion) => criterion.criterion_index)
      void execute({
        action,
        idempotencyKey: window.crypto.randomUUID(),
        request: {
          criteria_fingerprint: session.criteria_fingerprint,
          criterion_indexes: criterionIndexes,
          manual_approval: manualApproval,
        },
        sessionId,
      })
      return
    }
    void execute({
      action,
      idempotencyKey: window.crypto.randomUUID(),
      request: {},
      sessionId,
    })
  }

  function startNewSession(): void {
    setSessionId(null)
    setLoadInput('')
    setCheckedIndexes(new Set())
    setManualApproval(false)
    setPendingCommand(null)
    setError(null)
    setAnnouncement('Review the live repository and PR identity before creating a new session.')
    window.setTimeout(() => document.querySelector<HTMLInputElement>('#acceptance-repository')?.focus())
  }

  if (ticketQuery.isPending || reviewsQuery.isPending) {
    return (
      <Main>
        <LoadingState label='Loading acceptance panel context' />
      </Main>
    )
  }

  if (ticketQuery.isError) {
    return (
      <Main>
        <RequestErrorState error={ticketQuery.error} title='Ticket context failed' />
      </Main>
    )
  }

  if (reviewsQuery.isError) {
    return (
      <Main>
        <RequestErrorState
          error={reviewsQuery.error}
          title='Verification matrix request failed'
        />
      </Main>
    )
  }

  return (
    <Main>
      <div
        aria-busy={busy}
        className='flex min-w-0 flex-col gap-6'
        data-testid='acceptance-session-panel'
      >
        <header className='border-b pb-6'>
          <a
            href='/reviews'
            className='text-muted-foreground hover:text-foreground text-sm underline-offset-4 hover:underline'
          >
            Back to review queue
          </a>
          <p className='text-muted-foreground mt-4 text-sm font-medium'>
            Review acceptance · {ticketKey}
          </p>
          <h1
            ref={headingRef}
            tabIndex={-1}
            className='mt-1 text-2xl font-semibold tracking-normal outline-none'
          >
            Exact-head acceptance panel
          </h1>
          <p className='text-muted-foreground mt-2 max-w-3xl text-sm'>
            Atlas guides evidence, confirmation, and verification. The final action
            remains a deliberate manual operation in GitHub.
          </p>
        </header>

        <div ref={errorRef} tabIndex={-1} className='outline-none'>
          {error ? (
            <ErrorNotice
              error={error}
              pendingCommand={pendingCommand}
              busy={busy}
              onRefresh={() => void refreshCurrentState()}
              onRetry={(command) => void execute(command)}
            />
          ) : null}
        </div>

        {announcement ? (
          <Alert role='status' aria-live='polite'>
            {busy ? (
              <Clock3 aria-hidden='true' />
            ) : (
              <CheckCircle2 aria-hidden='true' />
            )}
            <AlertTitle>Acceptance panel update</AlertTitle>
            <AlertDescription>{announcement}</AlertDescription>
          </Alert>
        ) : null}

        {!sessionId ? (
          <SessionEntry
            busy={busy}
            defaultPr={defaultPr}
            loadInput={loadInput}
            onCreate={createSession}
            onLoad={loadSession}
            onLoadInputChange={setLoadInput}
            prInput={prInput}
            repository={repository}
            setPrInput={setPrInput}
            setRepository={setRepository}
          />
        ) : null}

        {sessionId && sessionQuery.isPending ? (
          <LoadingState label='Loading live acceptance readiness' />
        ) : null}

        {sessionId && sessionQuery.isError && !session ? (
          <RequestErrorState
            error={sessionQuery.error}
            title='Acceptance session GET failed'
          />
        ) : null}

        {session ? (
          <>
            <div className='flex flex-wrap gap-2'>
              <Button
                type='button'
                variant={
                  session.lifecycle === 'verification_passed' ? 'default' : 'outline'
                }
                disabled={busy}
                onClick={() => void refreshCurrentState()}
              >
                <RefreshCw aria-hidden='true' />
                {sessionQuery.isFetching ? 'Refreshing…' : 'Refresh current state'}
              </Button>
              {canStartNewSession ? (
                <Button type='button' disabled={busy} onClick={startNewSession}>
                  Start a new exact-head session
                </Button>
              ) : null}
            </div>

            <LiveReadiness
              current={readIsCurrent && sessionQuery.data?.merge_ready === true}
              reasons={liveReasons}
              session={session}
            />
            <SessionIdentity session={session} />
            <StepTimeline next={permittedAction} session={session} />
            <EvidenceSummary session={session} />
            {permittedAction === 'evidence' ? (
              <Button
                type='button'
                className='w-fit'
                disabled={busy}
                onClick={() => executeNext('evidence')}
              >
                Pull evidence
              </Button>
            ) : null}
            <CriteriaConfirmation
              busy={busy}
              checkedIndexes={checkedIndexes}
              manualApproval={manualApproval}
              next={permittedAction}
              onCheckedChange={(index, checked) => {
                setCheckedIndexes((current) => {
                  const next = new Set(current)
                  if (checked) next.add(index)
                  else next.delete(index)
                  return next
                })
              }}
              onManualApprovalChange={setManualApproval}
              onSubmit={() => executeNext('confirm')}
              session={session}
            />
            <VerificationSummary
              reviews={reviewsQuery.data.reviews}
              session={session}
            />
            {permittedAction === 'verify' ? (
              <Button
                type='button'
                className='w-fit'
                disabled={busy}
                onClick={() => executeNext('verify')}
              >
                Run verification
              </Button>
            ) : null}
            <ServerReasons reasons={session.blocking_reasons} />
            {creationReceipt ? <CreationReceiptCard receipt={creationReceipt} /> : null}
            {lastReceipt ? <ReceiptCard receipt={lastReceipt} /> : null}
            {session.receipts.length > 0 ? (
              <section aria-label='Session receipt inventory' className='space-y-2'>
                <h2 className='font-medium'>Session receipt inventory</h2>
                <ul className='grid gap-2 text-sm'>
                  {session.receipts.map((receiptId) => (
                    <li
                      className='border-border rounded-md border px-3 py-2 break-all'
                      key={receiptId}
                    >
                      {receiptId}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </>
        ) : null}
      </div>
    </Main>
  )
}

export function AcceptanceSessionView() {
  const params = useParams({ strict: false }) as { key?: string }
  return <AcceptanceSessionPanel ticketKey={params.key ?? ''} />
}
