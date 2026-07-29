import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import {
  PlaceholderRoute,
  ThrowingOperatorView,
} from '@/app-shell/route-components'
import { operatorSurfaces, type OperatorSurface } from '@/app-shell/surfaces'
import { RootRouteChrome } from '@/components/root-route-chrome'
import { OperatorLayout } from '@/components/layout/operator-layout'
import { GeneralError, RouteErrorBoundary } from '@/features/errors/general-error'
import { NotFoundError } from '@/features/errors/not-found-error'
import { TicketDetailPlaceholder } from '@/features/placeholders/ticket-detail-placeholder'

type CreateOperatorRouterOptions = {
  includeErrorProbe?: boolean
}

function getSurface(id: OperatorSurface['id']): OperatorSurface {
  const surface = operatorSurfaces.find((item) => item.id === id)
  if (!surface) {
    throw new Error(`Unknown operator surface: ${id}`)
  }
  return surface
}

function createOperatorRouteTree({
  includeErrorProbe = false,
}: CreateOperatorRouterOptions) {
  const rootRoute = createRootRoute({
    component: RootRouteChrome,
    notFoundComponent: () => (
      <OperatorLayout>
        <NotFoundError />
      </OperatorLayout>
    ),
    errorComponent: () => (
      <OperatorLayout>
        <GeneralError minimal />
      </OperatorLayout>
    ),
  })

  const operatorRoute = createRoute({
    getParentRoute: () => rootRoute,
    id: 'operator',
    component: OperatorLayout,
    errorComponent: RouteErrorBoundary,
  })

  const overview = getSurface('overview')
  const tickets = getSurface('tickets')
  const ticketDetail = getSurface('ticket-detail')
  const reviews = getSurface('reviews')
  const criticalPath = getSurface('critical-path')
  const dependencyGraph = getSurface('dependency-graph')
  const lessons = getSurface('lessons')

  const operatorChildren = [
    createRoute({
      getParentRoute: () => operatorRoute,
      path: overview.routePath,
      component: () => <PlaceholderRoute surface={overview} />,
      errorComponent: RouteErrorBoundary,
    }),
    createRoute({
      getParentRoute: () => operatorRoute,
      path: tickets.routePath,
      component: () => <PlaceholderRoute surface={tickets} />,
      errorComponent: RouteErrorBoundary,
    }),
    createRoute({
      getParentRoute: () => operatorRoute,
      path: ticketDetail.routePath,
      component: () => <TicketDetailPlaceholder surface={ticketDetail} />,
      errorComponent: RouteErrorBoundary,
    }),
    createRoute({
      getParentRoute: () => operatorRoute,
      path: reviews.routePath,
      component: () => <PlaceholderRoute surface={reviews} />,
      errorComponent: RouteErrorBoundary,
    }),
    createRoute({
      getParentRoute: () => operatorRoute,
      path: criticalPath.routePath,
      component: () => <PlaceholderRoute surface={criticalPath} />,
      errorComponent: RouteErrorBoundary,
    }),
    createRoute({
      getParentRoute: () => operatorRoute,
      path: dependencyGraph.routePath,
      component: () => <PlaceholderRoute surface={dependencyGraph} />,
      errorComponent: RouteErrorBoundary,
    }),
    createRoute({
      getParentRoute: () => operatorRoute,
      path: lessons.routePath,
      component: () => <PlaceholderRoute surface={lessons} />,
      errorComponent: RouteErrorBoundary,
    }),
  ]

  if (includeErrorProbe) {
    operatorChildren.push(
      createRoute({
        getParentRoute: () => operatorRoute,
        path: '__atlas-error-probe',
        component: ThrowingOperatorView,
        errorComponent: RouteErrorBoundary,
      })
    )
  }

  return rootRoute.addChildren([operatorRoute.addChildren(operatorChildren)])
}

export function createOperatorRouter(options: CreateOperatorRouterOptions = {}) {
  return createRouter({
    routeTree: createOperatorRouteTree(options),
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
