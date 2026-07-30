import { Link } from '@tanstack/react-router'
import { ArrowRight, GitBranch, Info } from 'lucide-react'
import { useDependencyCriticalPathQuery } from '@/api/query-hooks'
import { ticketDetailHref } from '@/app-shell/surfaces'
import { Main } from '@/components/layout/main'
import {
  EmptyCollectionState,
  LoadingState,
  RequestErrorState,
} from '@/components/states'
import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  type CriticalPathResponse,
  type CriticalPathStep,
  selectCriticalPathHead,
  selectCriticalPathSteps,
  selectCriticalPathTotalEffort,
} from './selectors'

function valueText(value: number): string {
  return String(value)
}

function SummaryMetric({
  children,
  label,
}: {
  children: React.ReactNode
  label: string
}) {
  return (
    <div className='border-border bg-card text-card-foreground rounded-lg border p-4'>
      <p className='text-muted-foreground text-sm font-medium'>{label}</p>
      <div className='mt-2 text-2xl font-semibold tracking-normal'>
        {children}
      </div>
    </div>
  )
}

function TicketKeyLink({
  children,
  step,
  testId,
}: {
  children: React.ReactNode
  step: CriticalPathStep
  testId?: string
}) {
  return (
    <Link
      to={ticketDetailHref(step.key)}
      data-testid={testId}
      className='text-primary inline-flex items-center gap-1 font-medium underline-offset-4 hover:underline'
    >
      {children}
      <ArrowRight aria-hidden='true' className='size-3.5' />
    </Link>
  )
}

function CriticalPathSummary({
  head,
  stepCount,
  totalEffort,
}: {
  head: CriticalPathStep | null
  stepCount: number
  totalEffort: number
}) {
  return (
    <section
      aria-label='Critical path summary'
      className='grid gap-3 sm:grid-cols-3'
    >
      <SummaryMetric label='Total effort'>
        <span
          data-testid='critical-path-total'
          className='font-mono tabular-nums'
        >
          {valueText(totalEffort)}
        </span>
      </SummaryMetric>
      <SummaryMetric label='Steps'>
        <span
          data-testid='critical-path-step-count'
          className='font-mono tabular-nums'
        >
          {valueText(stepCount)}
        </span>
      </SummaryMetric>
      <SummaryMetric label='Head'>
        {head ? (
          <TicketKeyLink step={head} testId='critical-path-head-link'>
            {head.key}
          </TicketKeyLink>
        ) : (
          <span className='text-muted-foreground text-base font-medium'>None</span>
        )}
      </SummaryMetric>
    </section>
  )
}

function CriticalPathTable({ steps }: { steps: CriticalPathStep[] }) {
  if (steps.length === 0) {
    return (
      <EmptyCollectionState
        title='No critical path'
        detail='No non-terminal tickets remain in the dependency graph.'
      />
    )
  }

  return (
    <section
      aria-labelledby='critical-path-chain-heading'
      className='border-border bg-card text-card-foreground overflow-hidden rounded-lg border'
    >
      <div className='border-b px-4 py-3'>
        <h2
          id='critical-path-chain-heading'
          className='text-base font-semibold tracking-normal'
        >
          Execution Chain
        </h2>
      </div>
      <Table aria-label='Critical path execution chain' className='table-fixed'>
        <TableHeader>
          <TableRow>
            <TableHead className='w-20'>Step</TableHead>
            <TableHead>Ticket</TableHead>
            <TableHead className='text-right'>Effort</TableHead>
            <TableHead className='text-right'>Cumulative</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {steps.map((step, index) => (
            <TableRow data-testid='critical-path-step' key={`${step.key}-${index}`}>
              <TableCell className='text-muted-foreground font-mono tabular-nums'>
                {`#${index + 1}`}
              </TableCell>
              <TableCell className='whitespace-normal'>
                <TicketKeyLink step={step} testId='critical-path-step-link'>
                  {step.key}
                </TicketKeyLink>
              </TableCell>
              <TableCell
                data-testid='critical-path-step-effort'
                className='text-right font-mono tabular-nums'
              >
                {valueText(step.effort)}
              </TableCell>
              <TableCell
                data-testid='critical-path-step-cumulative'
                className='text-right font-mono tabular-nums'
              >
                {valueText(step.cumulative_effort)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  )
}

export function CriticalPathView({
  response,
}: {
  response: CriticalPathResponse
}) {
  const steps = selectCriticalPathSteps(response)
  const head = selectCriticalPathHead(response)
  const totalEffort = selectCriticalPathTotalEffort(response)

  return (
    <Main>
      <div className='flex flex-col gap-6'>
        <div className='flex flex-col gap-3 border-b pb-6 sm:flex-row sm:items-end sm:justify-between'>
          <div className='space-y-1'>
            <p className='text-muted-foreground text-sm font-medium'>
              Dependencies
            </p>
            <h1 className='text-2xl font-semibold tracking-normal'>
              Critical Path
            </h1>
          </div>
          <Badge variant='outline' className='gap-1'>
            <GitBranch aria-hidden='true' className='size-3' />
            Advisory
          </Badge>
        </div>

        <div
          role='note'
          aria-label='Critical path advisory'
          className='border-border bg-card text-card-foreground rounded-lg border p-4'
        >
          <div className='flex items-start gap-3'>
            <Info
              aria-hidden='true'
              className='text-muted-foreground mt-0.5 size-4'
            />
            <p className='text-sm'>
              <strong>ADVISORY.</strong> The critical path does not gate
              dispatch.
            </p>
          </div>
        </div>

        <CriticalPathSummary
          head={head}
          stepCount={steps.length}
          totalEffort={totalEffort}
        />
        <CriticalPathTable steps={steps} />
      </div>
    </Main>
  )
}

export function CriticalPathRoute() {
  const criticalPathQuery = useDependencyCriticalPathQuery()
  const response = criticalPathQuery.data

  if (criticalPathQuery.isLoading) {
    return (
      <Main>
        <LoadingState label='Loading critical path' />
      </Main>
    )
  }

  if (criticalPathQuery.isError) {
    return (
      <Main>
        <RequestErrorState
          error={criticalPathQuery.error}
          title='Critical path request failed'
        />
      </Main>
    )
  }

  if (!response) {
    return (
      <Main>
        <LoadingState label='Loading critical path' />
      </Main>
    )
  }

  return <CriticalPathView response={response} />
}
