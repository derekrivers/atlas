import { useParams } from '@tanstack/react-router'
import { AtlasRequestError } from '@/api/client'
import { useTicketDetailQuery } from '@/api/query-hooks'
import {
  type OperatorSurface,
  ticketDetailHref,
} from '@/app-shell/surfaces'
import { Main } from '@/components/layout/main'
import { LoadingState, RequestErrorState } from '@/components/states'
import { OperatorViewPlaceholder } from '@/features/placeholders/operator-view-placeholder'

type TicketDetailPlaceholderProps = {
  surface: OperatorSurface
}

function nativeBodyText(body: unknown): string {
  if (typeof body === 'string') {
    return body
  }
  return JSON.stringify(body)
}

function NativeTicketNotFound({ body }: { body: unknown }) {
  return (
    <Main>
      <div className='flex flex-col gap-4'>
        <div className='space-y-1 border-b pb-6'>
          <p className='text-muted-foreground text-sm font-medium'>
            Ticket Detail
          </p>
          <h1 className='text-2xl font-semibold tracking-normal'>
            Ticket not found
          </h1>
        </div>
        <pre
          data-testid='native-detail-body'
          className='border-border bg-card text-card-foreground overflow-auto rounded-lg border p-4 text-sm'
        >
          {nativeBodyText(body)}
        </pre>
      </div>
    </Main>
  )
}

export function TicketDetailPlaceholder({
  surface,
}: TicketDetailPlaceholderProps) {
  const { key: ticketKey } = useParams({ strict: false }) as { key: string }
  const ticketQuery = useTicketDetailQuery(ticketKey)

  if (ticketQuery.isLoading) {
    return (
      <Main>
        <LoadingState label={`Loading ${ticketKey}`} />
      </Main>
    )
  }

  if (
    ticketQuery.isError &&
    ticketQuery.error instanceof AtlasRequestError &&
    ticketQuery.error.status === 404
  ) {
    return <NativeTicketNotFound body={ticketQuery.error.body} />
  }

  if (ticketQuery.isError) {
    return (
      <Main>
        <RequestErrorState
          error={ticketQuery.error}
          title='Ticket request failed'
        />
      </Main>
    )
  }

  return (
    <OperatorViewPlaceholder
      eyebrow={surface.placeholder.eyebrow}
      title={surface.placeholder.title}
      body={`${surface.placeholder.body} Route instance: ${ticketDetailHref(
        ticketKey
      )}.`}
    />
  )
}
