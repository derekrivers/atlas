import { Outlet } from '@tanstack/react-router'
import { NavigationProgress } from '@/components/navigation-progress'
import { Toaster } from '@/components/ui/sonner'

export function RootRouteChrome() {
  return (
    <>
      <NavigationProgress />
      <Outlet />
      <Toaster duration={5000} />
    </>
  )
}
