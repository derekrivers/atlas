export type OperatorSurfaceId =
  | 'overview'
  | 'tickets'
  | 'ticket-detail'
  | 'reviews'
  | 'critical-path'
  | 'dependency-graph'
  | 'lessons'

export type OperatorSurface = {
  id: OperatorSurfaceId
  title: string
  href: string
  routePath: string
  placeholder: {
    body: string
    eyebrow: string
    title: string
  }
}

export const OPERATOR_TICKET_DETAIL_PLACEHOLDER_KEY = 'ATLAS-1'

export const operatorSurfaces = [
  {
    id: 'overview',
    title: 'Overview',
    href: '/',
    routePath: '/',
    placeholder: {
      eyebrow: 'Overview',
      title: 'Operational Snapshot',
      body: 'Status, board, review, and path data are reserved for later view tickets.',
    },
  },
  {
    id: 'tickets',
    title: 'Tickets',
    href: '/tickets',
    routePath: 'tickets',
    placeholder: {
      eyebrow: 'Board',
      title: 'Ticket Board',
      body: 'The sortable board shell is present; live records are not requested here.',
    },
  },
  {
    id: 'ticket-detail',
    title: 'Ticket Detail',
    href: `/tickets/${OPERATOR_TICKET_DETAIL_PLACEHOLDER_KEY}`,
    routePath: 'tickets/$key',
    placeholder: {
      eyebrow: 'Ticket Detail',
      title: 'Ticket Definition',
      body: 'Definition, metadata, evidence, and dependency panels remain placeholders.',
    },
  },
  {
    id: 'reviews',
    title: 'Review Queue',
    href: '/reviews',
    routePath: 'reviews',
    placeholder: {
      eyebrow: 'Review Queue',
      title: 'Acceptance Review',
      body: 'Verification checks and evidence gates will render after their view ticket lands.',
    },
  },
  {
    id: 'critical-path',
    title: 'Critical Path',
    href: '/critical-path',
    routePath: 'critical-path',
    placeholder: {
      eyebrow: 'Dependencies',
      title: 'Critical Path',
      body: 'The path route is reserved without rendering dependency projections.',
    },
  },
  {
    id: 'dependency-graph',
    title: 'Dependency Graph',
    href: '/dependency-graph',
    routePath: 'dependency-graph',
    placeholder: {
      eyebrow: 'Dependencies',
      title: 'Dependency Graph',
      body: 'The whole-graph route is reserved; graph layout and filtering remain with the graph view ticket.',
    },
  },
  {
    id: 'lessons',
    title: 'Lessons',
    href: '/lessons',
    routePath: 'lessons',
    placeholder: {
      eyebrow: 'Knowledge',
      title: 'Lessons',
      body: 'Draft and active lesson tables will be wired by their view ticket.',
    },
  },
] as const satisfies readonly OperatorSurface[]

export function normaliseTicketKeySearch(value: string): string | undefined {
  const trimmed = value.trim().toUpperCase()
  if (/^ATLAS-\d+$/.test(trimmed)) {
    return trimmed
  }
  return undefined
}

export function ticketDetailHref(key: string): string {
  return `/tickets/${encodeURIComponent(key)}`
}
