import { useEffect, useRef, useState } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import type { components } from '@/api/atlas-openapi'
import {
  AtlasRequestError,
  isApiUnreachableError,
  type AtlasLessonDispositionResponse,
  type AtlasRouteResponse,
} from '@/api/client'
import {
  atlasQueryKeys,
  usePromoteLessonMutation,
  useRejectLessonMutation,
} from '@/api/query-hooks'
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useOperatorSession } from '@/context/operator-session-provider'

type Schema = components['schemas']
type LessonItem = Schema['LessonItemSchema']
type OperatorActionReceipt = Schema['OperatorActionReceiptSchema']
type LessonDispositionConflict = Schema['LessonDispositionConflictResponse']
type HTTPValidationError = Schema['HTTPValidationError']

type PromoteCommand = {
  action: 'promote'
  confidence: number
  idempotencyKey: string
}
type RejectCommand = {
  action: 'reject'
  idempotencyKey: string
}
type LessonCommand = PromoteCommand | RejectCommand
type Confirmation = LessonCommand['action'] | null

const LESSONS_QUERY_KEY = atlasQueryKeys.lessons()

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function errorDetail(error: AtlasRequestError): string | null {
  if (!isRecord(error.body) || typeof error.body.detail !== 'string') {
    return null
  }
  return error.body.detail
}

function conflictResponse(
  error: AtlasRequestError
): LessonDispositionConflict | null {
  if (!isRecord(error.body)) {
    return null
  }
  if (typeof error.body.detail !== 'string') {
    return null
  }
  const lesson = error.body.lesson
  if (lesson !== null && !isRecord(lesson)) {
    return null
  }
  return error.body as LessonDispositionConflict
}

function validationMessage(error: AtlasRequestError): string | null {
  if (!isRecord(error.body) || !Array.isArray(error.body.detail)) {
    return null
  }
  const validation = error.body as HTTPValidationError
  const confidenceIssue = validation.detail?.find((issue) =>
    issue.loc.some((part) => part === 'confidence')
  )
  return confidenceIssue?.msg ?? validation.detail?.[0]?.msg ?? null
}

function confidenceValue(value: string): number | null {
  if (value.trim() === '') {
    return null
  }
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 1) {
    return null
  }
  return parsed
}

function replaceLessonInCache(
  queryClient: QueryClient,
  lesson: LessonItem
): void {
  queryClient.setQueryData<AtlasRouteResponse<'/api/v1/lessons'>>(
    LESSONS_QUERY_KEY,
    (current) => {
      if (!current) {
        return current
      }
      return {
        lessons: current.lessons.map((item) =>
          item.id === lesson.id ? lesson : item
        ),
      }
    }
  )
}

function isAmbiguousFailure(error: unknown): boolean {
  return (
    isApiUnreachableError(error) ||
    (error instanceof AtlasRequestError && error.status >= 500)
  )
}

function ReceiptSummary({ receipt }: { receipt: OperatorActionReceipt }) {
  return (
    <section aria-label='Disposition receipt' className='space-y-2'>
      <h3 className='text-sm font-medium'>Server receipt</h3>
      <dl className='grid gap-2 text-sm sm:grid-cols-2'>
        <div>
          <dt className='text-muted-foreground'>Action</dt>
          <dd>{receipt.action}</dd>
        </div>
        <div>
          <dt className='text-muted-foreground'>Outcome</dt>
          <dd>{receipt.outcome}</dd>
        </div>
        <div>
          <dt className='text-muted-foreground'>Result</dt>
          <dd>{receipt.result_code}</dd>
        </div>
        <div>
          <dt className='text-muted-foreground'>Status change</dt>
          <dd>
            {receipt.before_status ?? 'none'} → {receipt.after_status ?? 'none'}
          </dd>
        </div>
        <div className='sm:col-span-2'>
          <dt className='text-muted-foreground'>Receipt ID</dt>
          <dd className='break-all font-mono text-xs'>{receipt.receipt_id}</dd>
        </div>
        <div className='sm:col-span-2'>
          <dt className='text-muted-foreground'>Completed</dt>
          <dd className='break-all'>{receipt.completed_at}</dd>
        </div>
      </dl>
    </section>
  )
}

export function LessonDispositionControls({
  lesson,
  onClose,
  onCommandLifecycleChange,
}: {
  lesson: LessonItem
  onClose: () => void
  onCommandLifecycleChange: (active: boolean) => void
}) {
  const queryClient = useQueryClient()
  const promoteMutation = usePromoteLessonMutation()
  const rejectMutation = useRejectLessonMutation()
  const session = useOperatorSession()
  const inFlight = useRef(false)
  const promoteTrigger = useRef<HTMLButtonElement>(null)
  const rejectTrigger = useRef<HTMLButtonElement>(null)
  const [confirmation, setConfirmation] = useState<Confirmation>(null)
  const [confidenceInput, setConfidenceInput] = useState('')
  const [confidenceError, setConfidenceError] = useState<string | null>(null)
  const [confirmationError, setConfirmationError] = useState<string | null>(null)
  const [pendingCommand, setPendingCommand] = useState<LessonCommand | null>(null)
  const [receipt, setReceipt] = useState<OperatorActionReceipt | null>(null)
  const [announcement, setAnnouncement] = useState<string | null>(null)
  const [commandError, setCommandError] = useState<string | null>(null)
  const [conflict, setConflict] = useState<LessonDispositionConflict | null>(null)
  const [requiresReview, setRequiresReview] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const busy = promoteMutation.isPending || rejectMutation.isPending

  useEffect(() => {
    inFlight.current = false
    setConfirmation(null)
    setConfidenceInput('')
    setConfidenceError(null)
    setConfirmationError(null)
    setPendingCommand(null)
    setReceipt(null)
    setAnnouncement(null)
    setCommandError(null)
    setConflict(null)
    setRequiresReview(false)
  }, [lesson.id])

  useEffect(() => {
    onCommandLifecycleChange(pendingCommand !== null)
    return () => onCommandLifecycleChange(false)
  }, [onCommandLifecycleChange, pendingCommand])

  function beginConfirmation(action: LessonCommand['action']) {
    if (!session.authenticated) {
      session.beginSessionFlow()
      return
    }
    setConfidenceError(null)
    setConfirmationError(null)
    setAnnouncement(null)
    setCommandError(null)
    setConfirmation(action)
  }

  async function handleCommandError(error: unknown, command: LessonCommand) {
    if (isAmbiguousFailure(error)) {
      setPendingCommand(command)
      setConfirmation(null)
      setCommandError(
        'Atlas did not return an unambiguous result. The same command key is retained for a safe retry and this drawer stays open; refresh the lesson before starting over.'
      )
      return
    }

    setPendingCommand(null)
    if (!(error instanceof AtlasRequestError)) {
      setConfirmation(null)
      setCommandError('The lesson ruling failed before Atlas returned a result.')
      return
    }

    if (error.status === 401) {
      setConfirmation(null)
      setCommandError(null)
      await session.expireSession()
      return
    }

    if (error.status === 403) {
      setConfirmation(null)
      setCommandError(
        `Security refusal: ${errorDetail(error) ?? 'Atlas refused the authenticated mutation.'}`
      )
      return
    }

    if (error.status === 409) {
      const current = conflictResponse(error)
      if (current?.lesson) {
        replaceLessonInCache(queryClient, current.lesson)
      }
      setConflict(
        current ?? {
          detail: errorDetail(error) ?? 'Atlas reported a lesson state conflict.',
          lesson: null,
        }
      )
      setRequiresReview(true)
      setConfirmation(null)
      await queryClient.refetchQueries({ exact: true, queryKey: LESSONS_QUERY_KEY })
      return
    }

    if (error.status === 422) {
      const message =
        validationMessage(error) ?? 'Atlas rejected the submitted command values.'
      if (command.action === 'promote') {
        setConfidenceError(message)
      } else {
        setConfirmationError(message)
      }
      return
    }

    setConfirmation(null)
    setCommandError(
      errorDetail(error) ?? `Atlas refused the lesson ruling with HTTP ${error.status}.`
    )
  }

  async function execute(command: LessonCommand) {
    if (inFlight.current) {
      return
    }
    inFlight.current = true
    setCommandError(null)
    setConfirmationError(null)
    try {
      const response: AtlasLessonDispositionResponse =
        command.action === 'promote'
          ? await promoteMutation.mutateAsync({
              idempotencyKey: command.idempotencyKey,
              lessonId: lesson.id,
              request: { confidence: command.confidence },
            })
          : await rejectMutation.mutateAsync({
              idempotencyKey: command.idempotencyKey,
              lessonId: lesson.id,
              request: {},
            })

      replaceLessonInCache(queryClient, response.lesson)
      setReceipt(response.receipt)
      setAnnouncement(
        `Atlas recorded ${response.receipt.action} with ${response.receipt.result_code}. The server returned lesson status is ${response.lesson.status}.`
      )
      setPendingCommand(null)
      setConfirmation(null)
      setConfidenceInput('')
      await queryClient.invalidateQueries({
        exact: true,
        queryKey: LESSONS_QUERY_KEY,
        refetchType: 'none',
      })
    } catch (error) {
      await handleCommandError(error, command)
    } finally {
      if (command.action === 'promote') {
        promoteMutation.reset()
      } else {
        rejectMutation.reset()
      }
      inFlight.current = false
    }
  }

  function confirmPromotion() {
    const confidence = confidenceValue(confidenceInput)
    if (confidence === null) {
      setConfidenceError('Enter a finite confidence from 0.0 through 1.0.')
      return
    }
    setConfidenceError(null)
    const command: PromoteCommand = {
      action: 'promote',
      confidence,
      idempotencyKey: window.crypto.randomUUID(),
    }
    setPendingCommand(command)
    void execute(command)
  }

  function confirmRejection() {
    const command: RejectCommand = {
      action: 'reject',
      idempotencyKey: window.crypto.randomUUID(),
    }
    setPendingCommand(command)
    void execute(command)
  }

  async function refreshBeforeStartingOver() {
    setRefreshing(true)
    try {
      await queryClient.refetchQueries(
        {
          exact: true,
          queryKey: LESSONS_QUERY_KEY,
          type: 'active',
        },
        { throwOnError: true }
      )
      setPendingCommand(null)
      setCommandError(null)
      setAnnouncement('The lesson queue was refreshed. Re-review before ruling.')
    } catch (_error) {
      setCommandError(
        'The lesson refresh failed. The original command key remains available for a safe retry.'
      )
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div className='space-y-4'>
      {announcement ? (
        <Alert role='status' aria-live='polite'>
          <AlertTitle>Lesson ruling recorded</AlertTitle>
          <AlertDescription>{announcement}</AlertDescription>
        </Alert>
      ) : null}

      {receipt ? <ReceiptSummary receipt={receipt} /> : null}

      {commandError ? (
        <Alert variant='destructive' aria-live='assertive'>
          <AlertTitle>Lesson ruling not completed</AlertTitle>
          <AlertDescription>
            <p>{commandError}</p>
            {pendingCommand ? (
              <div className='mt-2 flex flex-wrap gap-2'>
                <Button
                  type='button'
                  size='sm'
                  disabled={busy || refreshing}
                  onClick={() => void execute(pendingCommand)}
                >
                  Retry safely
                </Button>
                <Button
                  type='button'
                  size='sm'
                  variant='outline'
                  disabled={busy || refreshing}
                  onClick={() => void refreshBeforeStartingOver()}
                >
                  {refreshing ? 'Refreshing…' : 'Refresh lesson state'}
                </Button>
              </div>
            ) : null}
          </AlertDescription>
        </Alert>
      ) : null}

      {conflict ? (
        <Alert variant='destructive' aria-live='assertive'>
          <AlertTitle>Lesson changed; ruling blocked</AlertTitle>
          <AlertDescription>
            <p>{conflict.detail}</p>
            {conflict.lesson ? (
              <p>
                Safe current state: <strong>{conflict.lesson.status}</strong>
                {conflict.lesson.confidence === null
                  ? ''
                  : ` at confidence ${conflict.lesson.confidence}`}.
              </p>
            ) : (
              <p>The lesson queue was refreshed to obtain the safe current state.</p>
            )}
            <Button type='button' size='sm' variant='outline' onClick={onClose}>
              Close and re-review lesson
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}

      {lesson.status === 'draft' && !requiresReview && !pendingCommand ? (
        <section aria-label='Lesson ruling' className='space-y-3 border-t pt-4'>
          <div>
            <h3 className='text-sm font-medium'>Operator ruling</h3>
            <p className='text-muted-foreground text-sm'>
              Promotion and rejection are available only while the server reports this lesson as DRAFT.
            </p>
          </div>
          <div className='flex flex-wrap gap-2'>
            <Button
              ref={promoteTrigger}
              type='button'
              onClick={() => beginConfirmation('promote')}
            >
              Promote
            </Button>
            <Button
              ref={rejectTrigger}
              type='button'
              variant='destructive'
              onClick={() => beginConfirmation('reject')}
            >
              Reject
            </Button>
          </div>
        </section>
      ) : null}

      <AlertDialog
        open={confirmation === 'promote'}
        onOpenChange={(open) => {
          if (!open && !busy) {
            setConfirmation(null)
          }
        }}
      >
        <AlertDialogContent
          aria-busy={busy}
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            promoteTrigger.current?.focus()
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm lesson promotion</AlertDialogTitle>
            <AlertDialogDescription id='promote-context-pack-confirmation'>
              Promoting this DRAFT makes it ACTIVE. ACTIVE lessons may enter future context packs used by delivery agents.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className='grid gap-2'>
            <Label htmlFor={`lesson-confidence-${lesson.id}`}>
              Operator confidence (0.0–1.0)
            </Label>
            <Input
              id={`lesson-confidence-${lesson.id}`}
              type='number'
              min='0'
              max='1'
              step='0.001'
              inputMode='decimal'
              value={confidenceInput}
              disabled={busy}
              aria-describedby={
                confidenceError
                  ? 'promote-context-pack-confirmation lesson-confidence-error'
                  : 'promote-context-pack-confirmation'
              }
              aria-invalid={confidenceError ? true : undefined}
              onChange={(event) => {
                setConfidenceInput(event.target.value)
                setConfidenceError(null)
              }}
            />
            {confidenceError ? (
              <p id='lesson-confidence-error' className='text-destructive text-sm' role='alert'>
                {confidenceError}
              </p>
            ) : null}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <Button type='button' disabled={busy} onClick={confirmPromotion}>
              {busy ? 'Promoting…' : 'Confirm promotion'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={confirmation === 'reject'}
        onOpenChange={(open) => {
          if (!open && !busy) {
            setConfirmation(null)
          }
        }}
      >
        <AlertDialogContent
          aria-busy={busy}
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            rejectTrigger.current?.focus()
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm lesson rejection</AlertDialogTitle>
            <AlertDialogDescription>
              Rejecting this DRAFT archives it for audit. It will not become eligible for future context packs.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {confirmationError ? (
            <p
              id='lesson-reject-error'
              className='text-destructive text-sm'
              role='alert'
            >
              {confirmationError}
            </p>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <Button
              type='button'
              variant='destructive'
              className='border-destructive! bg-background! text-foreground! border-2 hover:bg-muted!'
              disabled={busy}
              aria-describedby={
                confirmationError ? 'lesson-reject-error' : undefined
              }
              aria-invalid={confirmationError ? true : undefined}
              onClick={confirmRejection}
            >
              {busy ? 'Rejecting…' : 'Confirm rejection'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
