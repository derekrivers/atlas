import { AlertCircle, Inbox, LoaderCircle, WifiOff } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ATLAS_API_BASE_URL } from '@/api/config'
import { AtlasRequestError } from '@/api/client'

type LoadingStateProps = {
  label?: string
}

type EmptyCollectionStateProps = {
  title: string
  detail?: string
}

type RequestErrorStateProps = {
  error: unknown
  onRetry?: () => void
  title?: string
}

type ApiUnreachableStateProps = {
  apiBaseUrl?: string
}

function errorMessage(error: unknown): string {
  if (error instanceof AtlasRequestError) {
    return error.message
  }
  if (error instanceof Error) {
    return error.message
  }
  return 'The request failed.'
}

export function LoadingState({
  label = 'Loading',
}: LoadingStateProps) {
  return (
    <div
      role='status'
      aria-live='polite'
      className='border-border bg-card text-card-foreground flex min-h-40 items-center justify-center rounded-lg border p-6'
    >
      <div className='flex items-center gap-3 text-sm font-medium'>
        <LoaderCircle
          aria-hidden='true'
          className='text-muted-foreground size-4 animate-spin'
        />
        <span>{label}</span>
      </div>
    </div>
  )
}

export function EmptyCollectionState({
  detail,
  title,
}: EmptyCollectionStateProps) {
  return (
    <div
      role='status'
      className='border-border bg-card text-card-foreground flex min-h-44 flex-col items-center justify-center rounded-lg border p-6 text-center'
    >
      <Inbox aria-hidden='true' className='text-muted-foreground size-6' />
      <h2 className='mt-3 text-base font-semibold tracking-normal'>{title}</h2>
      {detail ? (
        <p className='text-muted-foreground mt-1 max-w-prose text-sm'>
          {detail}
        </p>
      ) : null}
    </div>
  )
}

export function RequestErrorState({
  error,
  onRetry,
  title = 'Request failed',
}: RequestErrorStateProps) {
  return (
    <div
      role='alert'
      className='border-destructive/40 bg-card text-card-foreground flex min-h-44 flex-col items-start justify-center rounded-lg border p-6'
    >
      <div className='flex items-start gap-3'>
        <AlertCircle
          aria-hidden='true'
          className='text-destructive mt-0.5 size-5'
        />
        <div>
          <h2 className='text-base font-semibold tracking-normal'>{title}</h2>
          <p className='text-muted-foreground mt-1 text-sm'>
            {errorMessage(error)}
          </p>
        </div>
      </div>
      {onRetry ? (
        <Button type='button' variant='outline' className='mt-4' onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  )
}

export function ApiUnreachableState({
  apiBaseUrl = ATLAS_API_BASE_URL,
}: ApiUnreachableStateProps) {
  return (
    <div
      role='alert'
      aria-live='assertive'
      className='bg-background flex min-h-svh items-center justify-center p-6'
    >
      <div className='border-border bg-card text-card-foreground w-full max-w-xl rounded-lg border p-6'>
        <div className='flex items-start gap-3'>
          <WifiOff aria-hidden='true' className='text-destructive mt-0.5 size-6' />
          <div>
            <h1 className='text-xl font-semibold tracking-normal'>
              API unreachable
            </h1>
            <p className='text-muted-foreground mt-2 text-sm'>
              The Atlas API is not reachable at{' '}
              <code className='bg-muted text-foreground rounded px-1 py-0.5'>
                {apiBaseUrl}
              </code>
              .
            </p>
            <p className='text-muted-foreground mt-2 text-sm'>
              <code className='bg-muted text-foreground rounded px-1 py-0.5'>
                atlas api serve
              </code>{' '}
              may not be running.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
