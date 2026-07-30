import type { ReactNode } from 'react'
import { useParams } from '@tanstack/react-router'
import { AlertCircle, Check } from 'lucide-react'
import { AtlasRequestError } from '@/api/client'
import {
  useTicketDependenciesQuery,
  useTicketDetailQuery,
  useTicketEvidenceQuery,
} from '@/api/query-hooks'
import type { components } from '@/api/atlas-openapi'
import { ticketDetailHref } from '@/app-shell/surfaces'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs'
import { Main } from '@/components/layout/main'
import {
  EmptyCollectionState,
  LoadingState,
  RequestErrorState,
} from '@/components/states'

type TicketDetail = components['schemas']['TicketDetailResponse']
type TicketDependencies =
  components['schemas']['TicketDependenciesResponse']
type DependencyBlocker = TicketDependencies['blockers'][number]
type NotReadyReason = TicketDependencies['readiness']['reasons'][number]
type TicketEvidenceItem = components['schemas']['TicketEvidenceItemSchema']

type TicketDetailViewProps = {
  surfaceTitle: string
}

type EvidencePanelState =
  | { kind: 'error'; error: unknown }
  | { kind: 'loading' }
  | { kind: 'success'; evidence: TicketEvidenceItem[] }

type TicketDetailContentProps = {
  dependencies?: TicketDependencies
  dependenciesError?: unknown
  dependenciesLoading?: boolean
  evidenceState?: EvidencePanelState
  ticket: TicketDetail
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

const ticketKeyPattern = /^ATLAS-\d+$/

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

function codeLabel(code: string): string {
  const words = code.split('_').map((word) =>
    word.toLowerCase() === 'adr' ? 'ADR' : word
  )
  const [first = '', ...rest] = words
  if (!first) {
    return code
  }
  return [first.charAt(0).toUpperCase() + first.slice(1), ...rest].join(' ')
}

function isTicketKey(value: string): boolean {
  return ticketKeyPattern.test(value)
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

function TicketDependencyLink({
  children,
  keyValue,
  testId,
}: {
  children?: ReactNode
  keyValue: string
  testId: string
}) {
  return (
    <a
      data-testid={testId}
      className='text-primary break-all font-medium underline-offset-4 hover:underline focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
      href={ticketDetailHref(keyValue)}
    >
      {children ?? keyValue}
    </a>
  )
}

function ReasonMeta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className='text-muted-foreground text-xs font-medium uppercase'>
        {label}
      </dt>
      <dd className='mt-1 break-all text-sm'>{value}</dd>
    </div>
  )
}

function NotReadyReasonItem({ reason }: { reason: NotReadyReason }) {
  const isDangling = reason.code === 'dangling_target'
  return (
    <li
      data-testid='ticket-detail-readiness-reason'
      className='border-border rounded-lg border p-4'
    >
      <div className='flex flex-wrap items-center gap-2'>
        <span
          data-testid='ticket-detail-readiness-reason-label'
          className='text-sm font-semibold'
        >
          {codeLabel(reason.code)}
        </span>
        <code
          data-testid='ticket-detail-readiness-reason-code'
          className='bg-muted text-foreground rounded px-1.5 py-0.5 text-xs'
        >
          {reason.code}
        </code>
        {isDangling ? (
          <Badge
            data-testid='ticket-detail-dependency-defect'
            variant='destructive'
          >
            Defect
          </Badge>
        ) : null}
      </div>
      <p
        data-testid='ticket-detail-readiness-reason-message'
        className='text-muted-foreground mt-2 text-sm leading-6'
      >
        {reason.message}
      </p>
      {reason.target || reason.status ? (
        <dl className='mt-3 grid gap-3 @3xl/content:grid-cols-2'>
          {reason.target ? (
            <ReasonMeta label='Target' value={reason.target} />
          ) : null}
          {reason.status ? (
            <ReasonMeta label='Status' value={reason.status} />
          ) : null}
        </dl>
      ) : null}
    </li>
  )
}

function BlockerItem({ blocker }: { blocker: DependencyBlocker }) {
  const linked = isTicketKey(blocker.key)
  const isDangling = blocker.code === 'dangling_target'
  return (
    <li
      data-testid='ticket-detail-blockers-item'
      className='border-border flex min-h-12 flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-sm'
    >
      {linked ? (
        <TicketDependencyLink
          keyValue={blocker.key}
          testId='ticket-detail-blocker-link'
        />
      ) : (
        <span
          data-testid={
            isDangling
              ? 'ticket-detail-blocker-defect-target'
              : 'ticket-detail-blocker-target'
          }
          className='break-all font-medium'
        >
          {blocker.key}
        </span>
      )}
      <Badge variant='outline'>{codeLabel(blocker.code)}</Badge>
      {isDangling ? (
        <Badge data-testid='ticket-detail-blocker-defect' variant='destructive'>
          Defect
        </Badge>
      ) : null}
    </li>
  )
}

function BlockedByItem({ keyValue }: { keyValue: string }) {
  return (
    <li
      data-testid='ticket-detail-blocked-by-item'
      className='border-border flex min-h-12 items-center rounded-lg border px-3 py-2 text-sm'
    >
      <TicketDependencyLink
        keyValue={keyValue}
        testId='ticket-detail-blocked-by-link'
      />
    </li>
  )
}

function DependencySection({
  children,
  empty,
  testId,
  title,
}: {
  children: ReactNode
  empty: boolean
  testId: string
  title: string
}) {
  return (
    <section className='space-y-3'>
      <h2 className='text-sm font-semibold tracking-normal'>{title}</h2>
      {empty ? (
        <p
          data-testid={`${testId}-empty`}
          className='text-muted-foreground border-border rounded-lg border p-4 text-sm'
        >
          None
        </p>
      ) : (
        children
      )}
    </section>
  )
}

function pinTripleLabel(record: TicketEvidenceItem): string {
  return record.has_system_pin_triple
    ? 'System pin triple complete'
    : 'System pin triple incomplete'
}

function pinTripleDetail(record: TicketEvidenceItem): string {
  return record.has_system_pin_triple
    ? 'Commit, run, and hash pins are present.'
    : 'A required commit, run, or hash pin is missing.'
}

function EvidenceAttribute({
  label,
  testId,
  value,
}: {
  label: string
  testId: string
  value: string
}) {
  return (
    <div className='min-w-0'>
      <dt className='text-muted-foreground text-xs font-medium uppercase'>
        {label}
      </dt>
      <dd
        data-testid={testId}
        className='mt-1 break-words text-sm font-medium'
      >
        {value}
      </dd>
    </div>
  )
}

function EvidencePinTripleState({ record }: { record: TicketEvidenceItem }) {
  const Icon = record.has_system_pin_triple ? Check : AlertCircle

  return (
    <div
      data-pin-state={record.has_system_pin_triple ? 'complete' : 'incomplete'}
      data-testid='ticket-evidence-pin-state'
      className={cn(
        'flex items-start gap-3 rounded-lg border p-4',
        record.has_system_pin_triple
          ? 'border-primary/40 bg-primary/10 text-primary'
          : 'border-destructive/40 bg-destructive/10 text-destructive'
      )}
    >
      <Icon aria-hidden='true' className='mt-0.5 size-5 shrink-0' />
      <div className='min-w-0'>
        <p
          data-testid='ticket-evidence-pin-state-label'
          className='text-sm font-semibold tracking-normal'
        >
          {pinTripleLabel(record)}
        </p>
        <p className='mt-1 text-sm leading-5'>{pinTripleDetail(record)}</p>
      </div>
    </div>
  )
}

function EvidenceTrustTier({ tier }: { tier: TicketEvidenceItem['tier'] }) {
  return (
    <Badge
      data-testid='ticket-evidence-tier'
      data-tier={tier}
      variant={tier === 'system' ? 'default' : 'outline'}
      className={cn(
        'mt-1',
        tier === 'agent' ? 'bg-muted text-muted-foreground' : undefined
      )}
    >
      {tier}
    </Badge>
  )
}

function EvidenceRecord({ record }: { record: TicketEvidenceItem }) {
  return (
    <li
      data-testid='ticket-evidence-record'
      className='border-border bg-card text-card-foreground rounded-lg border p-4'
    >
      <EvidencePinTripleState record={record} />
      <dl className='mt-4 grid gap-4 @3xl/content:grid-cols-3'>
        <EvidenceAttribute
          label='Type'
          testId='ticket-evidence-type'
          value={record.type}
        />
        <div className='min-w-0'>
          <dt className='text-muted-foreground text-xs font-medium uppercase'>
            Trust Tier
          </dt>
          <dd>
            <EvidenceTrustTier tier={record.tier} />
          </dd>
        </div>
        <div className='min-w-0'>
          <dt className='text-muted-foreground text-xs font-medium uppercase'>
            Status
          </dt>
          <dd>
            <Badge
              data-testid='ticket-evidence-status'
              variant='secondary'
              className='mt-1'
            >
              {record.status}
            </Badge>
          </dd>
        </div>
      </dl>
    </li>
  )
}

export function TicketEvidenceTab({ state }: { state: EvidencePanelState }) {
  if (state.kind === 'loading') {
    return <LoadingState label='Loading evidence' />
  }

  if (state.kind === 'error') {
    return (
      <RequestErrorState
        error={state.error}
        title='Ticket evidence request failed'
      />
    )
  }

  if (state.evidence.length === 0) {
    return (
      <EmptyCollectionState
        title='No evidence stored'
        detail='This ticket has no stored evidence records.'
      />
    )
  }

  return (
    <ol data-testid='ticket-evidence-list' className='space-y-3'>
      {state.evidence.map((record, index) => (
        <EvidenceRecord
          key={`${record.type}-${record.tier}-${record.status}-${index}`}
          record={record}
        />
      ))}
    </ol>
  )
}

export function TicketDependenciesTab({
  dependencies,
  error,
  isLoading = false,
}: {
  dependencies?: TicketDependencies
  error?: unknown
  isLoading?: boolean
}) {
  if (isLoading && !dependencies) {
    return <LoadingState label='Loading dependencies' />
  }

  if (error && !dependencies) {
    return (
      <RequestErrorState
        error={error}
        title='Ticket dependencies request failed'
      />
    )
  }

  if (!dependencies) {
    return null
  }

  return (
    <div className='grid gap-4 @4xl/content:grid-cols-2'>
      <section className='border-border rounded-lg border p-4 @4xl/content:col-span-2'>
        <div className='flex flex-wrap items-center justify-between gap-3'>
          <h2 className='text-sm font-semibold tracking-normal'>Readiness</h2>
          <Badge
            data-testid='ticket-detail-readiness-verdict'
            variant={dependencies.readiness.ready ? 'secondary' : 'outline'}
          >
            {dependencies.readiness.ready ? 'Ready' : 'Not ready'}
          </Badge>
        </div>
        {dependencies.readiness.reasons.length > 0 ? (
          <ol
            data-testid='ticket-detail-readiness-reasons'
            className='mt-4 space-y-3'
          >
            {dependencies.readiness.reasons.map((reason, index) => (
              <NotReadyReasonItem
                key={`${reason.code}-${reason.target ?? 'self'}-${index}`}
                reason={reason}
              />
            ))}
          </ol>
        ) : null}
      </section>

      <DependencySection
        empty={dependencies.blockers.length === 0}
        testId='ticket-detail-blockers'
        title='Blockers'
      >
        <ul data-testid='ticket-detail-blockers' className='space-y-2'>
          {dependencies.blockers.map((blocker) => (
            <BlockerItem
              blocker={blocker}
              key={`${blocker.key}-${blocker.code}`}
            />
          ))}
        </ul>
      </DependencySection>

      <DependencySection
        empty={dependencies.blocked_by.length === 0}
        testId='ticket-detail-blocked-by'
        title='Blocked by'
      >
        <ul data-testid='ticket-detail-blocked-by' className='space-y-2'>
          {dependencies.blocked_by.map((keyValue) => (
            <BlockedByItem key={keyValue} keyValue={keyValue} />
          ))}
        </ul>
      </DependencySection>
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

function evidenceStateFromQuery(
  evidenceQuery: ReturnType<typeof useTicketEvidenceQuery>
): EvidencePanelState {
  if (evidenceQuery.isError) {
    return { kind: 'error', error: evidenceQuery.error }
  }

  if (!evidenceQuery.data) {
    return { kind: 'loading' }
  }

  return { kind: 'success', evidence: evidenceQuery.data.evidence }
}

export function TicketDetailContent({
  dependencies,
  dependenciesError,
  dependenciesLoading,
  evidenceState = { kind: 'success', evidence: [] },
  ticket,
}: TicketDetailContentProps) {
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
          data-testid='ticket-detail-evidence-panel'
          value='evidence'
        >
          <TicketEvidenceTab state={evidenceState} />
        </TabsContent>
        <TabsContent
          className='min-h-24'
          data-testid='ticket-detail-dependencies-panel'
          value='dependencies'
        >
          <TicketDependenciesTab
            dependencies={dependencies}
            error={dependenciesError}
            isLoading={dependenciesLoading}
          />
        </TabsContent>
      </Tabs>
    </Main>
  )
}

export function TicketDetailView({ surfaceTitle }: TicketDetailViewProps) {
  const { key: ticketKey } = useParams({ strict: false }) as { key: string }
  const ticketQuery = useTicketDetailQuery(ticketKey)
  const evidenceQuery = useTicketEvidenceQuery(ticketKey)
  const dependenciesQuery = useTicketDependenciesQuery(ticketKey)

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

  return (
    <TicketDetailContent
      dependencies={dependenciesQuery.data}
      dependenciesError={dependenciesQuery.error}
      dependenciesLoading={dependenciesQuery.isLoading}
      evidenceState={evidenceStateFromQuery(evidenceQuery)}
      ticket={ticketQuery.data}
    />
  )
}
