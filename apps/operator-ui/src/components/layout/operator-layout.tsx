import { Outlet } from '@tanstack/react-router'
import { getCookie } from '@/lib/cookies'
import { cn } from '@/lib/utils'
import { LayoutProvider } from '@/context/layout-provider'
import { SearchProvider } from '@/context/search-provider'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { AppSidebar } from '@/components/layout/app-sidebar'
import { Header } from '@/components/layout/header'
import { OperatorStatusFooter } from '@/components/layout/operator-status-footer'
import { Search } from '@/components/search'
import { SkipToMain } from '@/components/skip-to-main'
import { ThemeSwitch } from '@/components/theme-switch'

type OperatorLayoutProps = {
  children?: React.ReactNode
}

export function OperatorLayout({ children }: OperatorLayoutProps) {
  const defaultOpen = getCookie('sidebar_state') !== 'false'
  return (
    <SearchProvider>
      <LayoutProvider>
        <SidebarProvider defaultOpen={defaultOpen}>
          <SkipToMain />
          <AppSidebar />
          <SidebarInset
            className={cn(
              '@container/content',
              'flex min-h-svh flex-col',
              'has-data-[layout=fixed]:h-svh',
              'peer-data-[variant=inset]:has-data-[layout=fixed]:h-[calc(100svh-(var(--spacing)*4))]'
            )}
          >
            <Header fixed>
              <Search placeholder='Search routes' />
              <div className='ms-auto flex items-center gap-2'>
                <ThemeSwitch />
              </div>
            </Header>
            <div className='flex flex-1 flex-col'>{children ?? <Outlet />}</div>
            <OperatorStatusFooter />
          </SidebarInset>
        </SidebarProvider>
      </LayoutProvider>
    </SearchProvider>
  )
}
