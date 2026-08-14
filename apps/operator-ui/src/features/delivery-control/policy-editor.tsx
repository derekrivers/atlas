import { type FormEvent, useEffect, useRef, useState } from 'react'
import type { components } from '@/api/atlas-openapi'
import { atlasOpenApiEnums } from '@/api/atlas-openapi-runtime'
import {
  AtlasRequestError,
  isApiUnreachableError,
  type AtlasDeliveryAdmissionPolicyRequest,
} from '@/api/client'
import { useReplaceDeliveryAdmissionPolicyMutation } from '@/api/query-hooks'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useOperatorSession } from '@/context/operator-session-provider'
import {
  newComponentLane,
  newRiskLane,
  policyDraftFromPolicy,
  validatePolicyDraft,
  type PolicyDraft,
} from '@/features/delivery-control/policy-draft'

type Schema = components['schemas']
type Policy = Schema['DeliveryAdmissionPolicySchema']
type PolicyConflict = Schema['DeliveryAdmissionPolicyConflictResponse']
type PolicyReceipt = Schema['DeliveryPolicyActionReceiptSchema']
type RiskLevel = Schema['RiskLevel']

type PendingPolicyCommand = {
  idempotencyKey: string
  request: AtlasDeliveryAdmissionPolicyRequest
}

type RefreshResult = {
  data?: Schema['DeliveryControlResponse']
  error?: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function errorDetail(error: AtlasRequestError): string | null {
  return isRecord(error.body) && typeof error.body.detail === 'string'
    ? error.body.detail
    : null
}

function policyConflict(error: AtlasRequestError): PolicyConflict | null {
  if (!isRecord(error.body) || typeof error.body.detail !== 'string') {
    return null
  }
  return error.body as PolicyConflict
}

function serverValidationMessage(error: AtlasRequestError): string {
  if (!isRecord(error.body) || !Array.isArray(error.body.detail)) {
    return 'Atlas rejected one or more policy fields.'
  }
  const messages = error.body.detail
    .filter(isRecord)
    .map((item) => (typeof item.msg === 'string' ? item.msg : null))
    .filter((item): item is string => item !== null)
  return messages[0] ?? 'Atlas rejected one or more policy fields.'
}

function isAmbiguousFailure(error: unknown): boolean {
  return (
    isApiUnreachableError(error) ||
    (error instanceof AtlasRequestError && error.status >= 500)
  )
}

function modeLabel(mode: Schema['DeliveryAdmissionMode']): string {
  return mode.charAt(0).toUpperCase() + mode.slice(1)
}

function LaneSummary({ proposal }: { proposal: AtlasDeliveryAdmissionPolicyRequest }) {
  return (
    <div className='grid gap-4 sm:grid-cols-2'>
      <section aria-label='Proposed risk lane limits'>
        <h4 className='text-sm font-medium'>Risk lane limits</h4>
        {proposal.risk_lane_limits.length === 0 ? (
          <p className='text-muted-foreground text-sm'>No risk-specific limits.</p>
        ) : (
          <ul className='mt-1 space-y-1 text-sm'>
            {proposal.risk_lane_limits.map((lane) => (
              <li key={lane.risk_level} className='break-words'>
                <code>{lane.risk_level}</code>: maximum {lane.limit}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-label='Proposed component lane limits' className='min-w-0'>
        <h4 className='text-sm font-medium'>Component lane limits</h4>
        {proposal.component_lane_limits.length === 0 ? (
          <p className='text-muted-foreground text-sm'>No component-specific limits.</p>
        ) : (
          <ul className='mt-1 space-y-1 text-sm'>
            {proposal.component_lane_limits.map((lane, index) => (
              <li key={`${lane.component}-${index}`} className='break-all'>
                <code>{lane.component}</code>: maximum {lane.limit}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

export function PolicyProposalSummary({
  proposal,
}: {
  proposal: AtlasDeliveryAdmissionPolicyRequest
}) {
  return (
    <section aria-label='Complete proposed policy summary' className='space-y-4'>
      <dl className='grid gap-3 text-sm sm:grid-cols-2'>
        <div>
          <dt className='text-foreground'>Mode</dt>
          <dd className='font-medium'>{modeLabel(proposal.mode)}</dd>
        </div>
        <div>
          <dt className='text-foreground'>Approved policy ceiling</dt>
          <dd className='font-medium'>Maximum {proposal.approved_symphony_ceiling}</dd>
        </div>
        <div>
          <dt className='text-foreground'>Working budget</dt>
          <dd className='font-medium'>Maximum {proposal.working_budget}</dd>
        </div>
        <div>
          <dt className='text-foreground'>Review budget</dt>
          <dd className='font-medium'>Maximum {proposal.review_budget}</dd>
        </div>
        <div>
          <dt className='text-foreground'>Changes Requested reserve</dt>
          <dd className='font-medium'>Protected capacity {proposal.changes_requested_reserve}</dd>
        </div>
        <div>
          <dt className='text-foreground'>Expected policy revision</dt>
          <dd className='font-medium'>{proposal.expected_revision}</dd>
        </div>
      </dl>
      <LaneSummary proposal={proposal} />
    </section>
  )
}

function ReceiptSummary({ receipt }: { receipt: PolicyReceipt }) {
  return (
    <section aria-label='Policy replacement receipt' className='space-y-2'>
      <h3 className='text-sm font-medium'>Authoritative server receipt</h3>
      <dl className='grid gap-2 text-sm sm:grid-cols-2'>
        <div>
          <dt className='text-muted-foreground'>Action</dt>
          <dd>{receipt.action}</dd>
        </div>
        <div>
          <dt className='text-muted-foreground'>Result</dt>
          <dd>{receipt.result_code}</dd>
        </div>
        <div className='sm:col-span-2'>
          <dt className='text-muted-foreground'>Receipt ID</dt>
          <dd className='break-all font-mono text-xs'>{receipt.receipt_id}</dd>
        </div>
        <div className='sm:col-span-2'>
          <dt className='text-muted-foreground'>Completed</dt>
          <dd>{receipt.completed_at}</dd>
        </div>
      </dl>
    </section>
  )
}

function FieldError({ error, id }: { error?: string; id: string }) {
  return error ? (
    <p id={id} className='text-destructive text-sm' role='alert'>
      {error}
    </p>
  ) : null
}

export function PolicyEditor({
  policy,
  refreshCurrent,
}: {
  policy: Policy
  refreshCurrent: () => Promise<RefreshResult>
}) {
  const session = useOperatorSession()
  const mutation = useReplaceDeliveryAdmissionPolicyMutation()
  const trigger = useRef<HTMLButtonElement>(null)
  const inFlight = useRef(false)
  const loadedPolicyRevision = useRef(policy.revision)
  const [draft, setDraft] = useState<PolicyDraft>(() => policyDraftFromPolicy(policy))
  const [dirty, setDirty] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [proposal, setProposal] =
    useState<AtlasDeliveryAdmissionPolicyRequest | null>(null)
  const [confirmationOpen, setConfirmationOpen] = useState(false)
  const [summaryConfirmed, setSummaryConfirmed] = useState(false)
  const [pendingCommand, setPendingCommand] =
    useState<PendingPolicyCommand | null>(null)
  const [conflict, setConflict] = useState<PolicyConflict | null>(null)
  const [commandError, setCommandError] = useState<string | null>(null)
  const [announcement, setAnnouncement] = useState<string | null>(null)
  const [receipt, setReceipt] = useState<PolicyReceipt | null>(null)
  const [serverReturnedPolicy, setServerReturnedPolicy] = useState<Policy | null>(null)
  const [authoritativeRefreshRequired, setAuthoritativeRefreshRequired] =
    useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const busy = mutation.isPending || refreshing

  useEffect(() => {
    if (
      policy.revision > loadedPolicyRevision.current &&
      !dirty &&
      pendingCommand === null &&
      !confirmationOpen
    ) {
      setDraft(policyDraftFromPolicy(policy))
      loadedPolicyRevision.current = policy.revision
    }
  }, [confirmationOpen, dirty, pendingCommand, policy])

  function updateDraft(change: (current: PolicyDraft) => PolicyDraft) {
    setDirty(true)
    setErrors({})
    setCommandError(null)
    setConflict(null)
    setDraft(change)
  }

  function beginConfirmation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!session.authenticated) {
      session.beginSessionFlow()
      return
    }
    const validation = validatePolicyDraft(draft)
    setErrors(validation.errors)
    if (validation.proposal === null) {
      setAnnouncement(null)
      return
    }
    setProposal(validation.proposal)
    setSummaryConfirmed(false)
    setCommandError(null)
    setConflict(null)
    setConfirmationOpen(true)
  }

  async function handleCommandError(
    error: unknown,
    command: PendingPolicyCommand
  ) {
    if (isAmbiguousFailure(error)) {
      setPendingCommand(command)
      setConfirmationOpen(false)
      setCommandError(
        'Atlas did not return an unambiguous outcome. The exact proposal and command key are retained for an explicit same-command retry; no altered policy will be retried.'
      )
      return
    }

    setPendingCommand(null)
    setConfirmationOpen(false)
    if (!(error instanceof AtlasRequestError)) {
      setCommandError('The policy command failed before Atlas returned a result.')
      return
    }
    if (error.status === 401) {
      setCommandError(
        'The operator session expired. The entered proposal is preserved, but it must be reviewed and explicitly confirmed after sign-in.'
      )
      await session.expireSession()
      return
    }
    if (error.status === 403) {
      setCommandError(
        `Security failure: ${errorDetail(error) ?? 'Atlas refused the authenticated policy command.'} The proposal was not changed.`
      )
      return
    }
    if (error.status === 409) {
      const response = policyConflict(error)
      setConflict(
        response ?? {
          conflict_code: null,
          current_policy: null,
          detail: errorDetail(error) ?? 'Atlas reported a policy conflict.',
          receipt: null,
        }
      )
      setCommandError(null)
      try {
        await refreshCurrent()
      } catch (_refreshError) {
        // The explicit conflict remains the recovery authority.
      }
      return
    }
    if (error.status === 422) {
      setCommandError(
        `${serverValidationMessage(error)} The complete entered proposal is preserved.`
      )
      return
    }
    setCommandError(
      `${errorDetail(error) ?? `Atlas refused the policy command with HTTP ${error.status}.`} The entered proposal is preserved.`
    )
  }

  async function execute(command: PendingPolicyCommand) {
    if (inFlight.current) return
    inFlight.current = true
    setCommandError(null)
    setAnnouncement(null)
    try {
      let response
      try {
        response = await mutation.mutateAsync(command)
      } catch (error) {
        await handleCommandError(error, command)
        return
      }
      setReceipt(response.receipt)
      setServerReturnedPolicy(response.policy)
      setAuthoritativeRefreshRequired(true)
      setAnnouncement(
        `Atlas confirmed policy revision ${response.policy.revision} and returned receipt ${response.receipt.receipt_id}. The delivery-control snapshot is being refetched before it is presented as current.`
      )
      setPendingCommand(null)
      setConfirmationOpen(false)
      try {
        const refreshed = await refreshCurrent()
        if (
          refreshed.error ||
          !refreshed.data ||
          refreshed.data.policy.revision < response.policy.revision
        ) {
          throw new Error('Authoritative refetch was unavailable')
        }
        setDraft(policyDraftFromPolicy(refreshed.data.policy))
        loadedPolicyRevision.current = refreshed.data.policy.revision
        setDirty(false)
        setAuthoritativeRefreshRequired(false)
        setAnnouncement(
          `Atlas confirmed and refetched authoritative policy revision ${refreshed.data.policy.revision}. Receipt ${response.receipt.receipt_id} is recorded.`
        )
      } catch (_refreshError) {
        setCommandError(
          'The policy command succeeded, but the current delivery-control snapshot could not be refetched. The prior snapshot remains stale; use Refresh current state before another command.'
        )
      }
    } finally {
      mutation.reset()
      inFlight.current = false
    }
  }

  function confirmProposal() {
    if (!proposal || !summaryConfirmed) return
    const command = {
      idempotencyKey: window.crypto.randomUUID(),
      request: proposal,
    }
    setPendingCommand(command)
    void execute(command)
  }

  async function refreshBeforeNewCommand() {
    setRefreshing(true)
    try {
      const refreshed = await refreshCurrent()
      if (refreshed.error || !refreshed.data) {
        throw new Error('Delivery-control state was unavailable')
      }
      const currentPolicy = refreshed.data.policy
      setDraft((current) => ({
        ...current,
        expectedRevision: currentPolicy.revision,
      }))
      loadedPolicyRevision.current = currentPolicy.revision
      setPendingCommand(null)
      setConflict(null)
      setCommandError(null)
      setAuthoritativeRefreshRequired(false)
      setAnnouncement(
        'Current server state was refreshed. The entered proposal remains unchanged; inspect it and explicitly confirm a new command when ready.'
      )
    } catch (_error) {
      setCommandError(
        pendingCommand
          ? 'Refresh failed. The exact pending command remains available for a safe retry.'
          : 'Refresh failed. The entered proposal remains preserved and no new command was created.'
      )
    } finally {
      setRefreshing(false)
    }
  }

  function loadCurrentPolicy(current: Policy) {
    setDraft(policyDraftFromPolicy(current))
    loadedPolicyRevision.current = current.revision
    setDirty(false)
    setProposal(null)
    setPendingCommand(null)
    setConflict(null)
    setCommandError(null)
    setAuthoritativeRefreshRequired(false)
    setAnnouncement(
      `Loaded authoritative policy revision ${current.revision}. Review the complete form before creating a newly keyed command.`
    )
  }

  const availableRisks = (atlasOpenApiEnums.RiskLevel as readonly RiskLevel[]).filter(
    (risk) => !draft.riskLanes.some((lane) => lane.riskLevel === risk)
  )

  return (
    <section aria-labelledby='policy-editor-title' className='space-y-5'>
      <div>
        <h2 id='policy-editor-title' className='text-xl font-semibold'>
          Replace delivery policy
        </h2>
        <p className='text-muted-foreground mt-1 text-sm'>
          This is a complete Atlas policy replacement, never a patch. It changes admission authority only; it does not read or change WORKFLOW.md, Symphony configuration, occupied workers, or active sessions.
        </p>
      </div>

      {announcement ? (
        <Alert role='status' aria-live='polite'>
          <AlertTitle>Policy command update</AlertTitle>
          <AlertDescription>{announcement}</AlertDescription>
        </Alert>
      ) : null}

      {commandError ? (
        <Alert variant='destructive' aria-live='assertive'>
          <AlertTitle>Policy replacement needs recovery</AlertTitle>
          <AlertDescription>
            <p>{commandError}</p>
            {pendingCommand || authoritativeRefreshRequired ? (
              <div className='mt-3 flex flex-wrap gap-2'>
                {pendingCommand ? (
                  <Button
                    type='button'
                    size='sm'
                    disabled={busy}
                    onClick={() => void execute(pendingCommand)}
                  >
                    Retry exact command safely
                  </Button>
                ) : null}
                <Button
                  type='button'
                  size='sm'
                  variant='outline'
                  disabled={busy}
                  onClick={() => void refreshBeforeNewCommand()}
                >
                  {refreshing ? 'Refreshing…' : 'Refresh current state'}
                </Button>
              </div>
            ) : null}
          </AlertDescription>
        </Alert>
      ) : null}

      {conflict ? (
        <Alert variant='destructive' aria-live='assertive'>
          <AlertTitle>Policy command blocked</AlertTitle>
          <AlertDescription className='space-y-3'>
            <p>
              {conflict.detail} Conflict code:{' '}
              <code>{conflict.conflict_code ?? 'unspecified'}</code>.
            </p>
            <p>
              The entered proposal is preserved and was not replayed. Inspect the current policy, then explicitly load it before preparing a newly confirmed command.
            </p>
            {conflict.current_policy ? (
              <div className='space-y-2'>
                <p>
                  Server current revision {conflict.current_policy.revision}, mode{' '}
                  {conflict.current_policy.mode}, approved policy ceiling{' '}
                  {conflict.current_policy.approved_symphony_ceiling}.
                </p>
                <Button
                  type='button'
                  size='sm'
                  variant='outline'
                  onClick={() => loadCurrentPolicy(conflict.current_policy!)}
                >
                  Load and review current policy
                </Button>
              </div>
            ) : (
              <Button
                type='button'
                size='sm'
                variant='outline'
                onClick={() => void refreshBeforeNewCommand()}
              >
                Refresh current state
              </Button>
            )}
          </AlertDescription>
        </Alert>
      ) : null}

      {receipt ? <ReceiptSummary receipt={receipt} /> : null}
      {serverReturnedPolicy ? (
        <p className='text-muted-foreground text-sm'>
          Last server-returned policy revision: {serverReturnedPolicy.revision}.
        </p>
      ) : null}

      <form className='space-y-6' onSubmit={beginConfirmation}>
        <fieldset
          disabled={
            busy ||
            pendingCommand !== null ||
            conflict !== null ||
            authoritativeRefreshRequired
          }
          className='space-y-6'
        >
          <legend className='sr-only'>Complete delivery admission policy</legend>
          <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
            <div className='grid gap-2'>
              <Label htmlFor='policy-mode'>Mode</Label>
              <Select
                value={draft.mode}
                onValueChange={(value: Schema['DeliveryAdmissionMode']) =>
                  updateDraft((current) => ({ ...current, mode: value }))
                }
              >
                <SelectTrigger id='policy-mode' className='w-full'>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {atlasOpenApiEnums.DeliveryAdmissionMode.map((mode) => (
                    <SelectItem key={mode} value={mode}>
                      {modeLabel(mode)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {[
              ['approvedPolicyCeiling', 'Approved policy ceiling', draft.approvedPolicyCeiling, 1],
              ['workingBudget', 'Working budget', draft.workingBudget, 1],
              ['reviewBudget', 'Review budget', draft.reviewBudget, 1],
              ['changesRequestedReserve', 'Changes Requested reserve', draft.changesRequestedReserve, 0],
            ].map(([field, label, value, minimum]) => {
              const key = String(field) as
                | 'approvedPolicyCeiling'
                | 'workingBudget'
                | 'reviewBudget'
                | 'changesRequestedReserve'
              const errorId = `${key}-error`
              return (
                <div key={key} className='grid gap-2'>
                  <Label htmlFor={`policy-${key}`}>{label}</Label>
                  <Input
                    id={`policy-${key}`}
                    type='number'
                    min={Number(minimum)}
                    max='10'
                    step='1'
                    inputMode='numeric'
                    value={String(value)}
                    aria-invalid={errors[key] ? true : undefined}
                    aria-describedby={errors[key] ? errorId : undefined}
                    onChange={(event) =>
                      updateDraft((current) => ({
                        ...current,
                        [key]: event.target.value,
                      }))
                    }
                  />
                  <FieldError id={errorId} error={errors[key]} />
                </div>
              )
            })}
            <div className='grid gap-2'>
              <Label htmlFor='policy-expected-revision'>Expected policy revision</Label>
              <Input
                id='policy-expected-revision'
                value={draft.expectedRevision}
                readOnly
                aria-readonly='true'
              />
            </div>
          </div>

          <section aria-labelledby='risk-lane-editor' className='space-y-3'>
            <div className='flex flex-wrap items-center justify-between gap-2'>
              <div>
                <h3 id='risk-lane-editor' className='font-medium'>Risk lane limits</h3>
                <p className='text-muted-foreground text-sm'>
                  Exact policy constraints, not scoring recommendations.
                </p>
              </div>
              <Button
                type='button'
                variant='outline'
                size='sm'
                disabled={availableRisks.length === 0}
                onClick={() =>
                  updateDraft((current) => ({
                    ...current,
                    riskLanes: [
                      ...current.riskLanes,
                      newRiskLane(availableRisks[0]),
                    ],
                  }))
                }
              >
                Add risk limit
              </Button>
            </div>
            <FieldError id='risk-lanes-error' error={errors.riskLanes} />
            {draft.riskLanes.length === 0 ? (
              <p className='text-muted-foreground text-sm'>No risk-specific limits.</p>
            ) : (
              <div className='space-y-3'>
                {draft.riskLanes.map((lane) => {
                  const selectorError = errors[`risk-${lane.id}-selector`]
                  const limitError = errors[`risk-${lane.id}-limit`]
                  return (
                    <div key={lane.id} className='grid gap-3 rounded-lg border p-3 sm:grid-cols-[minmax(0,1fr)_8rem_auto]'>
                      <div className='grid gap-2'>
                        <Label htmlFor={`risk-${lane.id}`}>Risk level</Label>
                        <Select
                          value={lane.riskLevel}
                          onValueChange={(value: RiskLevel) =>
                            updateDraft((current) => ({
                              ...current,
                              riskLanes: current.riskLanes.map((item) =>
                                item.id === lane.id ? { ...item, riskLevel: value } : item
                              ),
                            }))
                          }
                        >
                          <SelectTrigger id={`risk-${lane.id}`} className='w-full' aria-invalid={selectorError ? true : undefined}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {atlasOpenApiEnums.RiskLevel.map((risk) => (
                              <SelectItem key={risk} value={risk}>{risk}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <FieldError id={`risk-${lane.id}-selector-error`} error={selectorError} />
                      </div>
                      <div className='grid gap-2'>
                        <Label htmlFor={`risk-limit-${lane.id}`}>Maximum</Label>
                        <Input
                          id={`risk-limit-${lane.id}`}
                          type='number'
                          min='0'
                          max='10'
                          step='1'
                          value={lane.limit}
                          aria-invalid={limitError ? true : undefined}
                          onChange={(event) =>
                            updateDraft((current) => ({
                              ...current,
                              riskLanes: current.riskLanes.map((item) =>
                                item.id === lane.id ? { ...item, limit: event.target.value } : item
                              ),
                            }))
                          }
                        />
                        <FieldError id={`risk-${lane.id}-limit-error`} error={limitError} />
                      </div>
                      <Button
                        type='button'
                        variant='outline'
                        className='self-end'
                        aria-label={`Remove ${lane.riskLevel} risk limit`}
                        onClick={() =>
                          updateDraft((current) => ({
                            ...current,
                            riskLanes: current.riskLanes.filter((item) => item.id !== lane.id),
                          }))
                        }
                      >
                        Remove
                      </Button>
                    </div>
                  )
                })}
              </div>
            )}
          </section>

          <section aria-labelledby='component-lane-editor' className='space-y-3'>
            <div className='flex flex-wrap items-center justify-between gap-2'>
              <div>
                <h3 id='component-lane-editor' className='font-medium'>Component lane limits</h3>
                <p className='text-muted-foreground text-sm'>
                  Canonical component constraints; long names remain visible in full.
                </p>
              </div>
              <Button
                type='button'
                variant='outline'
                size='sm'
                disabled={draft.componentLanes.length >= 64}
                onClick={() =>
                  updateDraft((current) => ({
                    ...current,
                    componentLanes: [...current.componentLanes, newComponentLane()],
                  }))
                }
              >
                Add component limit
              </Button>
            </div>
            <FieldError id='component-lanes-error' error={errors.componentLanes} />
            {draft.componentLanes.length === 0 ? (
              <p className='text-muted-foreground text-sm'>No component-specific limits.</p>
            ) : (
              <div className='space-y-3'>
                {draft.componentLanes.map((lane, index) => {
                  const selectorError = errors[`component-${lane.id}-selector`]
                  const limitError = errors[`component-${lane.id}-limit`]
                  return (
                    <div key={lane.id} className='grid gap-3 rounded-lg border p-3 sm:grid-cols-[minmax(0,1fr)_8rem_auto]'>
                      <div className='grid min-w-0 gap-2'>
                        <Label htmlFor={`component-${lane.id}`}>Component selector {index + 1}</Label>
                        <Input
                          id={`component-${lane.id}`}
                          value={lane.component}
                          maxLength={128}
                          aria-invalid={selectorError ? true : undefined}
                          onChange={(event) =>
                            updateDraft((current) => ({
                              ...current,
                              componentLanes: current.componentLanes.map((item) =>
                                item.id === lane.id ? { ...item, component: event.target.value } : item
                              ),
                            }))
                          }
                        />
                        <FieldError id={`component-${lane.id}-selector-error`} error={selectorError} />
                      </div>
                      <div className='grid gap-2'>
                        <Label htmlFor={`component-limit-${lane.id}`}>Maximum</Label>
                        <Input
                          id={`component-limit-${lane.id}`}
                          type='number'
                          min='0'
                          max='10'
                          step='1'
                          value={lane.limit}
                          aria-invalid={limitError ? true : undefined}
                          onChange={(event) =>
                            updateDraft((current) => ({
                              ...current,
                              componentLanes: current.componentLanes.map((item) =>
                                item.id === lane.id ? { ...item, limit: event.target.value } : item
                              ),
                            }))
                          }
                        />
                        <FieldError id={`component-${lane.id}-limit-error`} error={limitError} />
                      </div>
                      <Button
                        type='button'
                        variant='outline'
                        className='self-end'
                        aria-label={`Remove component limit ${index + 1}`}
                        onClick={() =>
                          updateDraft((current) => ({
                            ...current,
                            componentLanes: current.componentLanes.filter((item) => item.id !== lane.id),
                          }))
                        }
                      >
                        Remove
                      </Button>
                    </div>
                  )
                })}
              </div>
            )}
          </section>

          <div className='flex flex-wrap gap-2'>
            <Button ref={trigger} type='submit'>Review complete replacement</Button>
            <Button
              type='button'
              variant='outline'
              onClick={() => loadCurrentPolicy(policy)}
            >
              Reset to active policy
            </Button>
          </div>
        </fieldset>
      </form>

      <AlertDialog
        open={confirmationOpen}
        onOpenChange={(open) => {
          if (!busy) {
            setConfirmationOpen(open)
            if (!open) setSummaryConfirmed(false)
          }
        }}
      >
        <AlertDialogContent
          aria-busy={mutation.isPending}
          className='max-h-[90svh] overflow-y-auto sm:max-w-2xl'
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            trigger.current?.focus()
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm complete policy replacement</AlertDialogTitle>
            <AlertDialogDescription className='text-foreground'>
              Inspect every proposed maximum and the compare-and-set revision. Atlas will not change Symphony configuration or terminate active work.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {proposal ? <PolicyProposalSummary proposal={proposal} /> : null}
          <div className='flex items-start gap-3 rounded-lg border p-3'>
            <Checkbox
              id='confirm-complete-policy'
              checked={summaryConfirmed}
              disabled={mutation.isPending}
              onCheckedChange={(checked) => setSummaryConfirmed(checked === true)}
            />
            <Label htmlFor='confirm-complete-policy' className='leading-relaxed'>
              I reviewed this complete policy and explicitly authorise this exact replacement at the displayed expected revision.
            </Label>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={mutation.isPending}>Cancel</AlertDialogCancel>
            <Button
              type='button'
              disabled={!summaryConfirmed || mutation.isPending}
              onClick={confirmProposal}
            >
              {mutation.isPending ? 'Submitting…' : 'Confirm and submit'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}
