import { type ReactNode } from 'react'
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query'
import { createAtlasQueryClient } from '@/api/query-policy'
import { DirectionProvider } from '@/context/direction-provider'
import { FontProvider } from '@/context/font-provider'
import { ThemeProvider } from '@/context/theme-provider'
import { OperatorSessionProvider } from '@/context/operator-session-provider'

const defaultQueryClient = createAtlasQueryClient()

type AppProvidersProps = {
  children: ReactNode
  queryClient?: QueryClient
}

export function AppProviders({
  children,
  queryClient = defaultQueryClient,
}: AppProvidersProps) {
  return (
    <QueryClientProvider client={queryClient}>
      <OperatorSessionProvider>
        <ThemeProvider>
          <FontProvider>
            <DirectionProvider>{children}</DirectionProvider>
          </FontProvider>
        </ThemeProvider>
      </OperatorSessionProvider>
    </QueryClientProvider>
  )
}
