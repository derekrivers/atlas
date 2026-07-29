import { type ReactNode } from 'react'
import {
  AtlasRequestError,
  isApiUnreachableError,
} from '@/api/client'
import { useSystemStatusQuery } from '@/api/query-hooks'
import { ApiUnreachableState, RequestErrorState } from '@/components/states'

type ApiReachabilityBoundaryProps = {
  children: ReactNode
}

export function ApiReachabilityBoundary({
  children,
}: ApiReachabilityBoundaryProps) {
  const statusQuery = useSystemStatusQuery()

  if (statusQuery.isError && isApiUnreachableError(statusQuery.error)) {
    return <ApiUnreachableState apiBaseUrl={statusQuery.error.apiBaseUrl} />
  }

  if (statusQuery.isError && statusQuery.error instanceof AtlasRequestError) {
    return (
      <main className='bg-background min-h-svh p-6'>
        <RequestErrorState error={statusQuery.error} title='Status request failed' />
      </main>
    )
  }

  return children
}
