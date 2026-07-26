import { Outlet } from '@tanstack/react-router'
import { ApiReachabilityBoundary } from '@/components/api-reachability-boundary'
import { NavigationProgress } from '@/components/navigation-progress'
import { Toaster } from '@/components/ui/sonner'

export function RootRouteChrome() {
  return (
    <>
      <NavigationProgress />
      <ApiReachabilityBoundary>
        <Outlet />
      </ApiReachabilityBoundary>
      <Toaster duration={5000} />
    </>
  )
}
