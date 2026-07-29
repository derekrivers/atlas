import type { components } from '@/api/atlas-openapi'

type Schema = components['schemas']

export type EpicItem = Schema['EpicItemSchema']
export type TicketBoardItem = Schema['TicketBoardItemSchema']
export type TicketBoardMode = 'flat' | 'epic'
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
  epicKeys: string[]
  includeTerminal: boolean
  mode: TicketBoardMode
  query: string
  riskLevels: string[]
  sort: TicketBoardSort
  statuses: string[]
  ticketTypes: string[]
}

export type TicketStatusDistributionBucket = {
  count: number
  status: string
}

export type TicketBoardSearchParams = Pick<
  TicketBoardState,
  | 'epicKeys'
  | 'includeTerminal'
  | 'mode'
  | 'query'
  | 'riskLevels'
  | 'statuses'
  | 'ticketTypes'
> & {
  sort?: TicketBoardSort
}

export type TicketBoardEpicGroup = {
  epic: EpicItem | null
  epicKey: string | null
  filterValue: string
  label: string
  tickets: TicketBoardItem[]
}

export const DEFAULT_TICKET_BOARD_SORT: TicketBoardSort = {
  direction: 'asc',
  field: 'key',
}

export const DEFAULT_TICKET_BOARD_MODE: TicketBoardMode = 'flat'
export const TERMINAL_TICKET_STATUSES = new Set<string>(['done', 'rejected'])
export const UNASSIGNED_EPIC_FILTER_VALUE = 'unassigned'
export const UNASSIGNED_EPIC_GROUP_LABEL = 'Unassigned'

export const TICKET_BOARD_SEARCH_KEYS = [
  'epic',
  'mode',
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

function parseMode(value: string | null): TicketBoardMode {
  return value === 'epic' ? 'epic' : DEFAULT_TICKET_BOARD_MODE
}

export function isTerminalTicketStatus(status: string): boolean {
  return TERMINAL_TICKET_STATUSES.has(status)
}

export function createTicketBoardState({
  epicKeys,
  includeTerminal,
  mode,
  query,
  riskLevels,
  sort = DEFAULT_TICKET_BOARD_SORT,
  statuses,
  ticketTypes,
}: TicketBoardSearchParams): TicketBoardState {
  const normalisedStatuses = uniqueSorted(statuses)
  return {
    epicKeys: uniqueSorted(epicKeys),
    includeTerminal:
      includeTerminal ||
      normalisedStatuses.some((status) => isTerminalTicketStatus(status)),
    mode,
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
    epicKeys: paramsList(params, 'epic'),
    includeTerminal: params.get('terminal') === 'show',
    mode: parseMode(params.get('mode')),
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
  if (state.epicKeys.length > 0) {
    params.set('epic', state.epicKeys.join(','))
  }
  if (state.includeTerminal) {
    params.set('terminal', 'show')
  }
  if (state.mode !== DEFAULT_TICKET_BOARD_MODE) {
    params.set('mode', state.mode)
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
    if (state.epicKeys.length > 0) {
      const epicFilterValue = ticket.epic_key ?? UNASSIGNED_EPIC_FILTER_VALUE
      if (!state.epicKeys.includes(epicFilterValue)) {
        return false
      }
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

export function uniqueTicketEpicValues(
  tickets: readonly TicketBoardItem[]
): string[] {
  const values = new Set<string>()
  let hasUnassigned = false

  for (const ticket of tickets) {
    if (ticket.epic_key) {
      values.add(ticket.epic_key)
    } else {
      hasUnassigned = true
    }
  }

  const sorted = Array.from(values).sort(compareTicketKeys)
  return hasUnassigned ? [...sorted, UNASSIGNED_EPIC_FILTER_VALUE] : sorted
}

export function groupTicketsByEpic(
  tickets: readonly TicketBoardItem[],
  epics: readonly EpicItem[]
): TicketBoardEpicGroup[] {
  const epicByKey = new Map(epics.map((epic) => [epic.key, epic]))
  const grouped = new Map<string, TicketBoardItem[]>()

  for (const ticket of tickets) {
    const filterValue = ticket.epic_key ?? UNASSIGNED_EPIC_FILTER_VALUE
    grouped.set(filterValue, [...(grouped.get(filterValue) ?? []), ticket])
  }

  return Array.from(grouped.entries())
    .sort(([left], [right]) => {
      if (left === UNASSIGNED_EPIC_FILTER_VALUE) {
        return 1
      }
      if (right === UNASSIGNED_EPIC_FILTER_VALUE) {
        return -1
      }
      return compareTicketKeys(left, right)
    })
    .map(([filterValue, groupTickets]) => {
      if (filterValue === UNASSIGNED_EPIC_FILTER_VALUE) {
        return {
          epic: null,
          epicKey: null,
          filterValue,
          label: UNASSIGNED_EPIC_GROUP_LABEL,
          tickets: groupTickets,
        }
      }

      const epic = epicByKey.get(filterValue) ?? null
      return {
        epic,
        epicKey: filterValue,
        filterValue,
        label: epic ? epic.title : filterValue,
        tickets: groupTickets,
      }
    })
}

export function selectTicketStatusDistribution(
  tickets: readonly TicketBoardItem[]
): TicketStatusDistributionBucket[] {
  const counts = new Map<string, number>()
  for (const ticket of tickets) {
    counts.set(ticket.status, (counts.get(ticket.status) ?? 0) + 1)
  }

  return Array.from(counts.entries())
    .map(([status, count]) => ({ count, status }))
    .sort((left, right) => left.status.localeCompare(right.status))
}

export function selectTicketStatusDistributionTotal(
  distribution: readonly TicketStatusDistributionBucket[]
): number {
  return distribution.reduce((total, bucket) => total + bucket.count, 0)
}

export function formatTicketBoardLabel(value: string): string {
  return value
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}
