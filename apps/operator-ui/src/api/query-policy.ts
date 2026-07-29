import { QueryClient } from '@tanstack/react-query'

export const ATLAS_QUERY_STALE_TIME_MS = 10_000
export const ATLAS_QUERY_POLL_INTERVAL_MS = 30_000

export function createAtlasQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        refetchInterval: ATLAS_QUERY_POLL_INTERVAL_MS,
        retry: false,
        staleTime: ATLAS_QUERY_STALE_TIME_MS,
      },
    },
  })
}
