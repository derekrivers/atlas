import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { RootRouteChrome } from '@/components/root-route-chrome'
import { OperatorLayout } from '@/components/layout/operator-layout'
import { GeneralError } from '@/features/errors/general-error'
import { NotFoundError } from '@/features/errors/not-found-error'
import { OperatorViewPlaceholder } from '@/features/placeholders/operator-view-placeholder'

const rootRoute = createRootRoute({
  component: RootRouteChrome,
  notFoundComponent: NotFoundError,
  errorComponent: GeneralError,
})

const operatorRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'operator',
  component: OperatorLayout,
})

const overviewRoute = createRoute({
  getParentRoute: () => operatorRoute,
  path: '/',
  component: () => (
    <OperatorViewPlaceholder
      eyebrow='Overview'
      title='Operational Snapshot'
      body='Status, board, review, and path data are reserved for later view tickets.'
    />
  ),
})

const ticketsRoute = createRoute({
  getParentRoute: () => operatorRoute,
  path: 'tickets',
  component: () => (
    <OperatorViewPlaceholder
      eyebrow='Board'
      title='Ticket Board'
      body='The sortable board shell is present; live records are not requested here.'
    />
  ),
})

const ticketDetailRoute = createRoute({
  getParentRoute: () => operatorRoute,
  path: 'tickets/$key',
  component: () => (
    <OperatorViewPlaceholder
      eyebrow='Ticket Detail'
      title='Ticket Definition'
      body='Definition, metadata, evidence, and dependency panels remain placeholders.'
    />
  ),
})

const reviewsRoute = createRoute({
  getParentRoute: () => operatorRoute,
  path: 'reviews',
  component: () => (
    <OperatorViewPlaceholder
      eyebrow='Review Queue'
      title='Acceptance Review'
      body='Verification checks and evidence gates will render after the API client lands.'
    />
  ),
})

const criticalPathRoute = createRoute({
  getParentRoute: () => operatorRoute,
  path: 'critical-path',
  component: () => (
    <OperatorViewPlaceholder
      eyebrow='Dependencies'
      title='Critical Path'
      body='The path route is reserved without querying dependency projections.'
    />
  ),
})

const lessonsRoute = createRoute({
  getParentRoute: () => operatorRoute,
  path: 'lessons',
  component: () => (
    <OperatorViewPlaceholder
      eyebrow='Knowledge'
      title='Lessons'
      body='Draft and active lesson tables will be wired by their view ticket.'
    />
  ),
})

const routeTree = rootRoute.addChildren([
  operatorRoute.addChildren([
    overviewRoute,
    ticketsRoute,
    ticketDetailRoute,
    reviewsRoute,
    criticalPathRoute,
    lessonsRoute,
  ]),
])

export function createOperatorRouter() {
  return createRouter({
    routeTree,
    defaultPreload: 'intent',
    defaultPreloadStaleTime: 0,
  })
}

export type OperatorRouter = ReturnType<typeof createOperatorRouter>

declare module '@tanstack/react-router' {
  interface Register {
    router: OperatorRouter
  }
}
