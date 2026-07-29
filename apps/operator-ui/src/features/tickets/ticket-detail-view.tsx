import { useParams } from '@tanstack/react-router'
import { AtlasRequestError } from '@/api/client'
import { useTicketDetailQuery } from '@/api/query-hooks'
import type { components } from '@/api/atlas-openapi'
import { Badge } from '@/components/ui/badge'
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs'
import { Main } from '@/components/layout/main'
import { LoadingState, RequestErrorState } from '@/components/states'

type TicketDetail = components['schemas']['TicketDetailResponse']

type TicketDetailViewProps = {
  surfaceTitle: string
}

type DetailListProps = {
  items: string[]
  label: string
  testId: string
}

type MetadataFieldProps = {
  label: string
  testId: string
  value: number | string | null
}

function nativeBodyText(body: unknown): string {
  if (typeof body === 'string') {
    return body
  }
  return JSON.stringify(body)
}

function nullableText(value: number | string | null): string {
  if (value === null) {
    return 'None'
  }
  return String(value)
}

function DefinitionList({ items, label, testId }: DetailListProps) {
  return (
    <section className='border-border rounded-lg border p-4'>
      <h2 className='text-sm font-semibold tracking-normal'>{label}</h2>
      {items.length > 0 ? (
        <ol
          data-testid={testId}
          className='mt-3 list-decimal space-y-2 pl-5 text-sm leading-6'
        >
          {items.map((item, index) => (
            <li data-testid={`${testId}-item`} key={`${item}-${index}`}>
              {item}
            </li>
          ))}
        </ol>
      ) : (
        <p
          data-testid={`${testId}-empty`}
          className='text-muted-foreground mt-3 text-sm'
        >
          None
        </p>
      )}
    </section>
  )
}

function MetadataField({ label, testId, value }: MetadataFieldProps) {
  return (
    <div className='min-w-0 border-t py-3 first:border-t-0'>
      <dt className='text-muted-foreground text-xs font-medium uppercase'>
        {label}
      </dt>
      <dd
        data-testid={testId}
        className='mt-1 break-words text-sm font-medium'
      >
        {nullableText(value)}
      </dd>
    </div>
  )
}

function TagsField({ tags }: { tags: string[] }) {
  return (
    <div className='min-w-0 border-t py-3'>
      <dt className='text-muted-foreground text-xs font-medium uppercase'>
        Tags
      </dt>
      {tags.length > 0 ? (
        <dd data-testid='ticket-detail-tags' className='mt-2 flex flex-wrap gap-2'>
          {tags.map((tag, index) => (
            <Badge
              data-testid='ticket-detail-tags-item'
              key={`${tag}-${index}`}
              variant='outline'
            >
              {tag}
            </Badge>
          ))}
        </dd>
      ) : (
        <dd
          data-testid='ticket-detail-tags-empty'
          className='text-muted-foreground mt-1 text-sm font-medium'
        >
          None
        </dd>
      )}
    </div>
  )
}

function TicketHeader({ ticket }: { ticket: TicketDetail }) {
  return (
    <div className='space-y-3 border-b pb-6'>
      <p
        data-testid='ticket-detail-key'
        className='text-muted-foreground text-sm font-medium'
      >
        {ticket.key}
      </p>
      <div className='flex flex-col gap-3 @3xl/content:flex-row @3xl/content:items-start @3xl/content:justify-between'>
        <h1
          data-testid='ticket-detail-title'
          className='max-w-4xl text-2xl font-semibold tracking-normal'
        >
          {ticket.title}
        </h1>
        <div className='flex flex-wrap gap-2'>
          <Badge variant='secondary'>{ticket.status}</Badge>
          <Badge variant='outline'>{ticket.ticket_type}</Badge>
          <Badge variant='outline'>{ticket.risk_level}</Badge>
        </div>
      </div>
    </div>
  )
}

function DefinitionTab({ ticket }: { ticket: TicketDetail }) {
  return (
    <div
      data-testid='ticket-detail-definition-panel'
      className='grid gap-4 @4xl/content:grid-cols-2'
    >
      <section className='border-border rounded-lg border p-4 @4xl/content:col-span-2'>
        <h2 className='text-sm font-semibold tracking-normal'>Objective</h2>
        <p
          data-testid='ticket-detail-objective'
          className='mt-3 text-sm leading-6'
        >
          {ticket.objective}
        </p>
      </section>
      <section className='border-border rounded-lg border p-4 @4xl/content:col-span-2'>
        <h2 className='text-sm font-semibold tracking-normal'>Context</h2>
        <p
          data-testid='ticket-detail-context'
          className='mt-3 whitespace-pre-wrap text-sm leading-6'
        >
          {ticket.context}
        </p>
      </section>
      <DefinitionList
        items={ticket.relevant_docs}
        label='Relevant Docs'
        testId='ticket-detail-relevant-docs'
      />
      <DefinitionList
        items={ticket.acceptance_criteria}
        label='Acceptance Criteria'
        testId='ticket-detail-acceptance-criteria'
      />
      <DefinitionList
        items={ticket.non_goals}
        label='Non-goals'
        testId='ticket-detail-non-goals'
      />
      <DefinitionList
        items={ticket.implementation_notes}
        label='Implementation Notes'
        testId='ticket-detail-implementation-notes'
      />
      <DefinitionList
        items={ticket.test_requirements}
        label='Test Requirements'
        testId='ticket-detail-test-requirements'
      />
      <DefinitionList
        items={ticket.documentation_requirements}
        label='Documentation Requirements'
        testId='ticket-detail-documentation-requirements'
      />
      <DefinitionList
        items={ticket.definition_of_done}
        label='Definition of Done'
        testId='ticket-detail-definition-of-done'
      />
    </div>
  )
}

function MetadataTab({ ticket }: { ticket: TicketDetail }) {
  return (
    <dl
      data-testid='ticket-detail-metadata-panel'
      className='border-border grid rounded-lg border px-4 @4xl/content:grid-cols-2 @4xl/content:gap-x-8'
    >
      <MetadataField
        label='Status'
        testId='ticket-detail-status'
        value={ticket.status}
      />
      <MetadataField
        label='Ticket Type'
        testId='ticket-detail-ticket-type'
        value={ticket.ticket_type}
      />
      <MetadataField
        label='Risk Level'
        testId='ticket-detail-risk-level'
        value={ticket.risk_level}
      />
      <MetadataField
        label='Priority'
        testId='ticket-detail-priority'
        value={ticket.priority}
      />
      <MetadataField
        label='Estimated Effort'
        testId='ticket-detail-estimated-effort'
        value={ticket.estimated_effort}
      />
      <MetadataField
        label='Component'
        testId='ticket-detail-component'
        value={ticket.component}
      />
      <TagsField tags={ticket.tags} />
      <MetadataField
        label='Source Anchor'
        testId='ticket-detail-source-anchor'
        value={ticket.source_anchor}
      />
      <MetadataField
        label='Linear ID'
        testId='ticket-detail-external-linear-id'
        value={ticket.external_linear_id}
      />
      <MetadataField
        label='GitHub Issue ID'
        testId='ticket-detail-external-github-issue-id'
        value={ticket.external_github_issue_id}
      />
      <MetadataField
        label='Created At'
        testId='ticket-detail-created-at'
        value={ticket.created_at}
      />
      <MetadataField
        label='Updated At'
        testId='ticket-detail-updated-at'
        value={ticket.updated_at}
      />
      <MetadataField
        label='Completed At'
        testId='ticket-detail-completed-at'
        value={ticket.completed_at}
      />
    </dl>
  )
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

export function TicketDetailContent({ ticket }: { ticket: TicketDetail }) {
  return (
    <Main className='space-y-6'>
      <TicketHeader ticket={ticket} />
      <Tabs defaultValue='definition' className='gap-4'>
        <TabsList className='w-full justify-start overflow-x-auto @3xl/content:w-fit'>
          <TabsTrigger value='definition'>Definition</TabsTrigger>
          <TabsTrigger value='metadata'>Metadata</TabsTrigger>
          <TabsTrigger value='evidence'>Evidence</TabsTrigger>
          <TabsTrigger value='dependencies'>Dependencies</TabsTrigger>
        </TabsList>
        <TabsContent value='definition'>
          <DefinitionTab ticket={ticket} />
        </TabsContent>
        <TabsContent value='metadata'>
          <MetadataTab ticket={ticket} />
        </TabsContent>
        <TabsContent
          className='min-h-24'
          data-testid='ticket-detail-evidence-panel'
          value='evidence'
        />
        <TabsContent
          className='min-h-24'
          data-testid='ticket-detail-dependencies-panel'
          value='dependencies'
        />
      </Tabs>
    </Main>
  )
}

export function TicketDetailView({ surfaceTitle }: TicketDetailViewProps) {
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
          title={`${surfaceTitle} request failed`}
        />
      </Main>
    )
  }

  if (!ticketQuery.data) {
    return (
      <Main>
        <LoadingState label={`Loading ${ticketKey}`} />
      </Main>
    )
  }

  return <TicketDetailContent ticket={ticketQuery.data} />
}
