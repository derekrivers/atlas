import { useSystemStatusQuery } from '@/api/query-hooks'
import { StalenessIndicator } from '@/components/staleness-indicator'

export function OperatorStatusFooter() {
  const statusQuery = useSystemStatusQuery()

  if (statusQuery.isError) {
    return (
      <footer
        aria-label='Status staleness'
        className='border-border bg-background/95 text-muted-foreground mt-auto border-t px-4 py-2 text-xs'
      >
        <p>Status staleness: unavailable</p>
      </footer>
    )
  }

  if (!statusQuery.data) {
    return (
      <footer
        aria-label='Status staleness'
        className='border-border bg-background/95 text-muted-foreground mt-auto border-t px-4 py-2 text-xs'
      >
        <p>Status staleness: loading</p>
      </footer>
    )
  }

  return (
    <footer
      aria-label='Status staleness'
      className='border-border bg-background/95 text-muted-foreground mt-auto border-t px-4 py-2 text-xs'
    >
      <div className='flex flex-wrap gap-x-4 gap-y-1'>
        <StalenessIndicator
          className='text-xs'
          label='Linear sync'
          testId='footer-linear-sync-staleness'
          value={statusQuery.data.last_linear_sync_at}
        />
        <StalenessIndicator
          className='text-xs'
          label='Evidence pull'
          testId='footer-evidence-pull-staleness'
          value={statusQuery.data.last_evidence_pull_at}
        />
      </div>
    </footer>
  )
}
