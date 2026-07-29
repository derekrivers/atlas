import type { components } from '@/api/atlas-openapi'

type Schema = components['schemas']

export type TicketBoardItem = Schema['TicketBoardItemSchema']
export type SortDirection = 'asc' | 'desc'
export type SortField =
  | 'key'
  | 'title'
  | 'status'
  | 'ticket_type'
  | 'priority'
  | 'risk_level'

export type TicketBoardSort = {
  field: SortField
  direction: SortDirection
}

export type TicketBoardState = {
  includeTerminal: boolean
  query: string
  riskLevels: string[]
  sort: TicketBoardSort
  statuses: string[]
  ticketTypes: string[]
}

export type TicketBoardSearchParams = Pick<
  TicketBoardState,
  'includeTerminal' | 'query' | 'riskLevels' | 'statuses' | 'ticketTypes'
> & {
  sort?: TicketBoardSort
}

export const DEFAULT_TICKET_BOARD_SORT: TicketBoardSort = {
  direction: 'asc',
  field: 'key',
}

export const TERMINAL_TICKET_STATUSES = new Set<string>(['done', 'rejected'])

export const TICKET_BOARD_SEARCH_KEYS = [
  'q',
  'risk',
  'sort',
  'status',
  'terminal',
  'type',
] as const

const SORT_FIELDS = new Set<string>([
  'key',
  'title',
  'status',
  'ticket_type',
  'priority',
  'risk_level',
])

function uniqueSorted(values: readonly string[]): string[] {
  return Array.from(new Set(values.filter(Boolean))).sort((a, b) =>
    a.localeCompare(b)
  )
}

function paramsList(params: URLSearchParams, key: string): string[] {
  return uniqueSorted(
    params
      .getAll(key)
      .flatMap((value) => value.split(','))
      .map((value) => value.trim())
  )
}

function parseSort(value: string | null): TicketBoardSort {
  if (!value) {
    return DEFAULT_TICKET_BOARD_SORT
  }

  const [field, direction] = value.split('.')
  if (!field || !SORT_FIELDS.has(field)) {
    return DEFAULT_TICKET_BOARD_SORT
  }

  return {
    direction: direction === 'desc' ? 'desc' : 'asc',
    field: field as SortField,
  }
}

export function isTerminalTicketStatus(status: string): boolean {
  return TERMINAL_TICKET_STATUSES.has(status)
}

export function createTicketBoardState({
  includeTerminal,
  query,
  riskLevels,
  sort = DEFAULT_TICKET_BOARD_SORT,
  statuses,
  ticketTypes,
}: TicketBoardSearchParams): TicketBoardState {
  const normalisedStatuses = uniqueSorted(statuses)
  return {
    includeTerminal:
      includeTerminal ||
      normalisedStatuses.some((status) => isTerminalTicketStatus(status)),
    query: query.trim(),
    riskLevels: uniqueSorted(riskLevels),
    sort,
    statuses: normalisedStatuses,
    ticketTypes: uniqueSorted(ticketTypes),
  }
}

export function parseTicketBoardSearch(
  search = window.location.search
): TicketBoardState {
  const params = new URLSearchParams(search)
  return createTicketBoardState({
    includeTerminal: params.get('terminal') === 'show',
    query: params.get('q') ?? '',
    riskLevels: paramsList(params, 'risk'),
    sort: parseSort(params.get('sort')),
    statuses: paramsList(params, 'status'),
    ticketTypes: paramsList(params, 'type'),
  })
}

export function writeTicketBoardSearch(state: TicketBoardState): void {
  const params = new URLSearchParams(window.location.search)

  for (const key of TICKET_BOARD_SEARCH_KEYS) {
    params.delete(key)
  }

  if (state.query) {
    params.set('q', state.query)
  }
  if (state.statuses.length > 0) {
    params.set('status', state.statuses.join(','))
  }
  if (state.ticketTypes.length > 0) {
    params.set('type', state.ticketTypes.join(','))
  }
  if (state.riskLevels.length > 0) {
    params.set('risk', state.riskLevels.join(','))
  }
  if (state.includeTerminal) {
    params.set('terminal', 'show')
  }
  if (
    state.sort.field !== DEFAULT_TICKET_BOARD_SORT.field ||
    state.sort.direction !== DEFAULT_TICKET_BOARD_SORT.direction
  ) {
    params.set('sort', `${state.sort.field}.${state.sort.direction}`)
  }

  const nextSearch = params.toString()
  const nextUrl = `${window.location.pathname}${nextSearch ? `?${nextSearch}` : ''}`
  window.history.pushState({}, '', nextUrl)
}

function ticketKeyParts(key: string): { prefix: string; number: number } | null {
  const match = /^(.+?)-(\d+)$/.exec(key)
  if (!match) {
    return null
  }
  return {
    number: Number(match[2]),
    prefix: match[1],
  }
}

export function compareTicketKeys(left: string, right: string): number {
  const leftParts = ticketKeyParts(left)
  const rightParts = ticketKeyParts(right)

  if (leftParts && rightParts && leftParts.prefix === rightParts.prefix) {
    return leftParts.number - rightParts.number
  }

  return left.localeCompare(right, undefined, { numeric: true })
}

function compareText(left: string, right: string): number {
  return left.localeCompare(right, undefined, {
    numeric: true,
    sensitivity: 'base',
  })
}

function compareTicketsByField(
  left: TicketBoardItem,
  right: TicketBoardItem,
  field: SortField
): number {
  if (field === 'key') {
    return compareTicketKeys(left.key, right.key)
  }
  if (field === 'priority') {
    return left.priority - right.priority
  }
  return compareText(String(left[field]), String(right[field]))
}

export function sortTickets(
  tickets: readonly TicketBoardItem[],
  sort: TicketBoardSort
): TicketBoardItem[] {
  const directionMultiplier = sort.direction === 'desc' ? -1 : 1
  return [...tickets].sort((left, right) => {
    const primary = compareTicketsByField(left, right, sort.field)
    const secondary = compareTicketKeys(left.key, right.key)
    return (primary || secondary) * directionMultiplier
  })
}

export function filterTickets(
  tickets: readonly TicketBoardItem[],
  state: TicketBoardState
): TicketBoardItem[] {
  const query = state.query.toLowerCase()
  return tickets.filter((ticket) => {
    if (!state.includeTerminal && isTerminalTicketStatus(ticket.status)) {
      return false
    }
    if (state.statuses.length > 0 && !state.statuses.includes(ticket.status)) {
      return false
    }
    if (
      state.ticketTypes.length > 0 &&
      !state.ticketTypes.includes(ticket.ticket_type)
    ) {
      return false
    }
    if (
      state.riskLevels.length > 0 &&
      !state.riskLevels.includes(ticket.risk_level)
    ) {
      return false
    }
    if (
      query &&
      !`${ticket.key} ${ticket.title}`.toLowerCase().includes(query)
    ) {
      return false
    }
    return true
  })
}

export function selectVisibleTickets(
  tickets: readonly TicketBoardItem[],
  state: TicketBoardState
): TicketBoardItem[] {
  return sortTickets(filterTickets(tickets, state), state.sort)
}

export function uniqueTicketValues(
  tickets: readonly TicketBoardItem[],
  field: 'risk_level' | 'status' | 'ticket_type'
): string[] {
  return uniqueSorted(tickets.map((ticket) => String(ticket[field])))
}

export function formatTicketBoardLabel(value: string): string {
  return value
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}
