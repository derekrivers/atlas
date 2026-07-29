import { useEffect, useMemo, useState } from 'react'
import { Link } from '@tanstack/react-router'
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Check,
  Eye,
  EyeOff,
  Search,
} from 'lucide-react'
import { useTicketsQuery } from '@/api/query-hooks'
import {
  AtlasRequestError,
  isApiUnreachableError,
} from '@/api/client'
import { ticketDetailHref } from '@/app-shell/surfaces'
import { cn } from '@/lib/utils'
import { Main } from '@/components/layout/main'
import {
  ApiUnreachableState,
  EmptyCollectionState,
  LoadingState,
  RequestErrorState,
} from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  createTicketBoardState,
  filterTickets,
  formatTicketBoardLabel,
  isTerminalTicketStatus,
  parseTicketBoardSearch,
  selectVisibleTickets,
  uniqueTicketValues,
  writeTicketBoardSearch,
  type SortField,
  type TicketBoardItem,
  type TicketBoardSort,
  type TicketBoardState,
} from './ticket-board-state'

const BOARD_COLUMNS = [
  { field: 'key', label: 'Key' },
  { field: 'title', label: 'Title' },
  { field: 'status', label: 'Status' },
  { field: 'ticket_type', label: 'Type' },
  { field: 'priority', label: 'Priority' },
  { field: 'risk_level', label: 'Risk' },
] as const satisfies readonly { field: SortField; label: string }[]

type TicketBoardFilter = {
  label: string
  param: 'riskLevels' | 'statuses' | 'ticketTypes'
  title: string
  values: string[]
}

type TicketBoardFilterMenuProps = Omit<TicketBoardFilter, 'param'> & {
  onChange: (values: string[]) => void
  selectedValues: string[]
}

type TicketBoardTableProps = {
  onSortChange: (sort: TicketBoardSort) => void
  sort: TicketBoardSort
  tickets: TicketBoardItem[]
}

type TicketBoardToolbarProps = {
  allTickets: TicketBoardItem[]
  onChange: (updater: (previous: TicketBoardState) => TicketBoardState) => void
  state: TicketBoardState
  visibleCount: number
}

function filterLabel(count: number, label: string): string {
  return count > 0 ? `${label}: ${count}` : label
}

function toggleValue(values: readonly string[], value: string): string[] {
  if (values.includes(value)) {
    return values.filter((item) => item !== value)
  }
  return [...values, value].sort((left, right) => left.localeCompare(right))
}

function toggleSort(current: TicketBoardSort, field: SortField): TicketBoardSort {
  if (current.field !== field) {
    return { direction: 'asc', field }
  }
  return {
    direction: current.direction === 'asc' ? 'desc' : 'asc',
    field,
  }
}

function sortIcon(sort: TicketBoardSort, field: SortField) {
  if (sort.field !== field) {
    return <ArrowUpDown aria-hidden='true' className='size-3.5' />
  }
  if (sort.direction === 'asc') {
    return <ArrowUp aria-hidden='true' className='size-3.5' />
  }
  return <ArrowDown aria-hidden='true' className='size-3.5' />
}

function FilterMenu({
  label,
  onChange,
  selectedValues,
  title,
  values,
}: TicketBoardFilterMenuProps) {
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant='outline' size='sm' className='h-8'>
          {filterLabel(selectedValues.length, label)}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align='start' className='w-56'>
        <DropdownMenuLabel>{title}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {values.map((value) => (
          <DropdownMenuCheckboxItem
            key={value}
            checked={selectedValues.includes(value)}
            onCheckedChange={() => onChange(toggleValue(selectedValues, value))}
          >
            {formatTicketBoardLabel(value)}
          </DropdownMenuCheckboxItem>
        ))}
        {selectedValues.length > 0 ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => onChange([])}>
              <Check aria-hidden='true' className='size-4 opacity-0' />
              Clear {label.toLowerCase()}
            </DropdownMenuItem>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function TicketBoardToolbar({
  allTickets,
  onChange,
  state,
  visibleCount,
}: TicketBoardToolbarProps) {
  const filterMenus: TicketBoardFilter[] = [
    {
      label: 'Status',
      param: 'statuses',
      title: 'Filter status',
      values: uniqueTicketValues(allTickets, 'status'),
    },
    {
      label: 'Type',
      param: 'ticketTypes',
      title: 'Filter type',
      values: uniqueTicketValues(allTickets, 'ticket_type'),
    },
    {
      label: 'Risk',
      param: 'riskLevels',
      title: 'Filter risk',
      values: uniqueTicketValues(allTickets, 'risk_level'),
    },
  ]

  const terminalCount = allTickets.filter((ticket) =>
    isTerminalTicketStatus(ticket.status)
  ).length

  return (
    <div className='flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between'>
      <div className='flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-center'>
        <div className='relative w-full sm:max-w-xs'>
          <Search
            aria-hidden='true'
            className='text-muted-foreground pointer-events-none absolute start-2.5 top-1/2 size-4 -translate-y-1/2'
          />
          <Input
            aria-label='Search tickets'
            className='h-8 ps-8'
            placeholder='Search key or title'
            value={state.query}
            onChange={(event) => {
              onChange((previous) =>
                createTicketBoardState({
                  ...previous,
                  query: event.target.value,
                })
              )
            }}
          />
        </div>
        <div className='flex flex-wrap gap-2'>
          {filterMenus.map((filter) => (
            <FilterMenu
              key={filter.param}
              label={filter.label}
              selectedValues={state[filter.param]}
              title={filter.title}
              values={filter.values}
              onChange={(values) => {
                onChange((previous) =>
                  createTicketBoardState({
                    ...previous,
                    includeTerminal:
                      previous.includeTerminal ||
                      values.some((value) => isTerminalTicketStatus(value)),
                    [filter.param]: values,
                  })
                )
              }}
            />
          ))}
        </div>
      </div>
      <div className='flex flex-wrap items-center gap-2'>
        <p className='text-muted-foreground text-sm' aria-live='polite'>
          {visibleCount} of {allTickets.length}
        </p>
        <Button
          type='button'
          variant={state.includeTerminal ? 'secondary' : 'outline'}
          size='sm'
          className='h-8'
          onClick={() => {
            onChange((previous) =>
              createTicketBoardState({
                ...previous,
                includeTerminal: !previous.includeTerminal,
                statuses: previous.includeTerminal
                  ? previous.statuses.filter(
                      (status) => !isTerminalTicketStatus(status)
                    )
                  : previous.statuses,
              })
            )
          }}
        >
          {state.includeTerminal ? (
            <EyeOff aria-hidden='true' className='size-4' />
          ) : (
            <Eye aria-hidden='true' className='size-4' />
          )}
          {state.includeTerminal ? 'Hide terminal' : `Show terminal (${terminalCount})`}
        </Button>
      </div>
    </div>
  )
}

function TicketBoardTable({
  onSortChange,
  sort,
  tickets,
}: TicketBoardTableProps) {
  return (
    <div className='border-border overflow-hidden rounded-lg border'>
      <Table>
        <TableHeader>
          <TableRow className='bg-muted/50 hover:bg-muted/50'>
            {BOARD_COLUMNS.map((column) => (
              <TableHead
                key={column.field}
                aria-sort={
                  sort.field === column.field
                    ? sort.direction === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : 'none'
                }
                className={cn(column.field === 'title' && 'min-w-72')}
              >
                <Button
                  variant='ghost'
                  size='sm'
                  className='h-8 px-2'
                  onClick={() => onSortChange(toggleSort(sort, column.field))}
                >
                  {column.label}
                  {sortIcon(sort, column.field)}
                </Button>
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {tickets.length > 0 ? (
            tickets.map((ticket) => (
              <TableRow key={ticket.key} data-testid='ticket-board-row'>
                <TableCell className='font-medium'>
                  <Link
                    className='text-primary underline-offset-4 hover:underline focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
                    to={ticketDetailHref(ticket.key)}
                  >
                    {ticket.key}
                  </Link>
                </TableCell>
                <TableCell className='max-w-[32rem] whitespace-normal'>
                  {ticket.title}
                </TableCell>
                <TableCell>
                  <Badge variant='outline'>
                    {formatTicketBoardLabel(ticket.status)}
                  </Badge>
                </TableCell>
                <TableCell>{formatTicketBoardLabel(ticket.ticket_type)}</TableCell>
                <TableCell>{ticket.priority}</TableCell>
                <TableCell>
                  <Badge variant='secondary'>
                    {formatTicketBoardLabel(ticket.risk_level)}
                  </Badge>
                </TableCell>
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell
                colSpan={BOARD_COLUMNS.length}
                className='text-muted-foreground h-24 text-center'
              >
                No tickets match.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  )
}

export function TicketBoard({ tickets }: { tickets: TicketBoardItem[] }) {
  const [boardState, setBoardState] = useState(() => parseTicketBoardSearch())

  useEffect(() => {
    const handlePopState = () => setBoardState(parseTicketBoardSearch())
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  const updateBoardState = (
    updater: (previous: TicketBoardState) => TicketBoardState
  ) => {
    setBoardState((previous) => {
      const next = updater(previous)
      writeTicketBoardSearch(next)
      return next
    })
  }

  const visibleTickets = useMemo(
    () => selectVisibleTickets(tickets, boardState),
    [boardState, tickets]
  )
  const matchingTickets = useMemo(
    () => filterTickets(tickets, boardState),
    [boardState, tickets]
  )

  return (
    <Main fluid>
      <div className='flex flex-col gap-5'>
        <div className='flex flex-col gap-3 border-b pb-5 sm:flex-row sm:items-end sm:justify-between'>
          <div className='space-y-1'>
            <p className='text-muted-foreground text-sm font-medium'>Board</p>
            <h1 className='text-2xl font-semibold tracking-normal'>
              Ticket Board
            </h1>
          </div>
          <Badge variant='outline' className='w-fit'>
            {matchingTickets.length} matching
          </Badge>
        </div>
        <TicketBoardToolbar
          allTickets={tickets}
          state={boardState}
          visibleCount={visibleTickets.length}
          onChange={updateBoardState}
        />
        <TicketBoardTable
          sort={boardState.sort}
          tickets={visibleTickets}
          onSortChange={(sort) => {
            updateBoardState((previous) =>
              createTicketBoardState({
                ...previous,
                sort,
              })
            )
          }}
        />
      </div>
    </Main>
  )
}

export function TicketBoardRoute() {
  const ticketsQuery = useTicketsQuery()

  if (ticketsQuery.isPending) {
    return (
      <Main fluid>
        <LoadingState label='Loading ticket board' />
      </Main>
    )
  }

  if (ticketsQuery.isError) {
    if (isApiUnreachableError(ticketsQuery.error)) {
      return <ApiUnreachableState apiBaseUrl={ticketsQuery.error.apiBaseUrl} />
    }
    return (
      <Main fluid>
        <RequestErrorState
          error={
            ticketsQuery.error instanceof AtlasRequestError
              ? ticketsQuery.error
              : new Error('Ticket board request failed')
          }
          title='Ticket board request failed'
        />
      </Main>
    )
  }

  if (ticketsQuery.data.tickets.length === 0) {
    return (
      <Main fluid>
        <EmptyCollectionState
          title='No tickets'
          detail='The Atlas store did not return ticket records.'
        />
      </Main>
    )
  }

  return <TicketBoard tickets={ticketsQuery.data.tickets} />
}
