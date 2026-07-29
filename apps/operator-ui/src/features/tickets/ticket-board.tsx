import { useEffect, useMemo, useState } from 'react'
import { Link } from '@tanstack/react-router'
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Check,
  Eye,
  EyeOff,
  ListTree,
  Search,
  Table2,
} from 'lucide-react'
import { useEpicsQuery, useTicketsQuery } from '@/api/query-hooks'
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
  groupTicketsByEpic,
  isTerminalTicketStatus,
  parseTicketBoardSearch,
  selectVisibleTickets,
  uniqueTicketEpicValues,
  uniqueTicketValues,
  writeTicketBoardSearch,
  UNASSIGNED_EPIC_FILTER_VALUE,
  type EpicItem,
  type SortField,
  type TicketBoardEpicGroup,
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
  formatValue?: (value: string) => string
  label: string
  param: 'epicKeys' | 'riskLevels' | 'statuses' | 'ticketTypes'
  title: string
  values: string[]
}

type TicketBoardFilterMenuProps = Omit<TicketBoardFilter, 'param'> & {
  onChange: (values: string[]) => void
  selectedValues: string[]
}

type TicketBoardTableProps = {
  framed?: boolean
  onSortChange: (sort: TicketBoardSort) => void
  sort: TicketBoardSort
  tickets: TicketBoardItem[]
}

type TicketBoardToolbarProps = {
  allTickets: TicketBoardItem[]
  epics: EpicItem[]
  onChange: (updater: (previous: TicketBoardState) => TicketBoardState) => void
  state: TicketBoardState
  visibleCount: number
}

type TicketBoardEpicGroupsProps = {
  groups: TicketBoardEpicGroup[]
  onSortChange: (sort: TicketBoardSort) => void
  sort: TicketBoardSort
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
  formatValue = formatTicketBoardLabel,
  label,
  onChange,
  selectedValues,
  title,
  values,
}: TicketBoardFilterMenuProps) {
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label={`Filter ${label.toLowerCase()}`}
          variant='outline'
          size='sm'
          className='h-8'
        >
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
            {formatValue(value)}
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
  epics,
  onChange,
  state,
  visibleCount,
}: TicketBoardToolbarProps) {
  const epicsByKey = new Map(epics.map((epic) => [epic.key, epic]))
  const formatEpicFilterValue = (value: string): string => {
    if (value === UNASSIGNED_EPIC_FILTER_VALUE) {
      return 'Unassigned'
    }
    const epic = epicsByKey.get(value)
    return epic ? `${epic.key} - ${epic.title}` : value
  }

  const filterMenus: TicketBoardFilter[] = [
    {
      formatValue: formatEpicFilterValue,
      label: 'Epic',
      param: 'epicKeys',
      title: 'Filter epic',
      values: uniqueTicketEpicValues(allTickets),
    },
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
              formatValue={filter.formatValue}
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
          aria-pressed={state.mode === 'epic'}
          variant={state.mode === 'epic' ? 'secondary' : 'outline'}
          size='sm'
          className='h-8'
          onClick={() => {
            onChange((previous) =>
              createTicketBoardState({
                ...previous,
                mode: previous.mode === 'epic' ? 'flat' : 'epic',
              })
            )
          }}
        >
          {state.mode === 'epic' ? (
            <Table2 aria-hidden='true' className='size-4' />
          ) : (
            <ListTree aria-hidden='true' className='size-4' />
          )}
          {state.mode === 'epic' ? 'Flat table' : 'Group by epic'}
        </Button>
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
  framed = true,
  onSortChange,
  sort,
  tickets,
}: TicketBoardTableProps) {
  const table = (
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
  )

  if (!framed) {
    return table
  }

  return (
    <div className='border-border overflow-hidden rounded-lg border'>
      {table}
    </div>
  )
}

function TicketBoardEpicGroups({
  groups,
  onSortChange,
  sort,
}: TicketBoardEpicGroupsProps) {
  if (groups.length === 0) {
    return (
      <TicketBoardTable
        onSortChange={onSortChange}
        sort={sort}
        tickets={[]}
      />
    )
  }

  return (
    <div className='space-y-4' data-testid='ticket-board-epic-groups'>
      {groups.map((group) => (
        <section
          key={group.filterValue}
          aria-labelledby={`ticket-board-epic-${group.filterValue}`}
          className='border-border overflow-hidden rounded-lg border'
          data-testid='ticket-board-epic-group'
          data-epic-key={group.filterValue}
        >
          <div className='bg-muted/20 flex flex-col gap-3 border-b px-4 py-3 sm:flex-row sm:items-start sm:justify-between'>
            <div className='min-w-0 space-y-1'>
              <div className='flex flex-wrap items-center gap-2'>
                <h2
                  id={`ticket-board-epic-${group.filterValue}`}
                  className='text-base font-semibold tracking-normal'
                >
                  {group.epicKey ?? 'Unassigned'}
                </h2>
                <Badge
                  variant='outline'
                  data-testid='ticket-board-epic-group-count'
                >
                  {group.tickets.length} tickets
                </Badge>
              </div>
              <p className='text-muted-foreground text-sm'>
                {group.epic ? group.label : 'Tickets without an epic'}
              </p>
            </div>
            {group.epic ? (
              <div className='flex flex-wrap items-center gap-2'>
                <Badge variant='outline'>
                  {formatTicketBoardLabel(group.epic.status)}
                </Badge>
                <Badge variant='secondary'>
                  {formatTicketBoardLabel(group.epic.risk_level)}
                </Badge>
                <span className='text-muted-foreground text-sm'>
                  P{group.epic.priority}
                </span>
              </div>
            ) : null}
          </div>
          <TicketBoardTable
            framed={false}
            sort={sort}
            tickets={group.tickets}
            onSortChange={onSortChange}
          />
        </section>
      ))}
    </div>
  )
}

export function TicketBoard({
  epics,
  tickets,
}: {
  epics: EpicItem[]
  tickets: TicketBoardItem[]
}) {
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
  const epicGroups = useMemo(
    () => groupTicketsByEpic(visibleTickets, epics),
    [epics, visibleTickets]
  )
  const handleSortChange = (sort: TicketBoardSort) => {
    updateBoardState((previous) =>
      createTicketBoardState({
        ...previous,
        sort,
      })
    )
  }

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
          epics={epics}
          state={boardState}
          visibleCount={visibleTickets.length}
          onChange={updateBoardState}
        />
        {boardState.mode === 'epic' ? (
          <TicketBoardEpicGroups
            groups={epicGroups}
            sort={boardState.sort}
            onSortChange={handleSortChange}
          />
        ) : (
          <TicketBoardTable
            sort={boardState.sort}
            tickets={visibleTickets}
            onSortChange={handleSortChange}
          />
        )}
      </div>
    </Main>
  )
}

export function TicketBoardRoute() {
  const epicsQuery = useEpicsQuery()
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

  return (
    <TicketBoard
      epics={epicsQuery.data?.epics ?? []}
      tickets={ticketsQuery.data.tickets}
    />
  )
}
