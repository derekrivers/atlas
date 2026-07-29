import { useSystemStatusQuery } from '@/api/query-hooks'

function formatStaleness(value: string | null | undefined): string {
  if (!value) {
    return 'never'
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

export function OperatorStatusFooter() {
  const statusQuery = useSystemStatusQuery()

  let content = 'Status staleness: loading'
  if (statusQuery.isError) {
    content = 'Status staleness: unavailable'
  } else if (statusQuery.data) {
    content = `Linear sync: ${formatStaleness(
      statusQuery.data.last_linear_sync_at
    )} | Evidence pull: ${formatStaleness(statusQuery.data.last_evidence_pull_at)}`
  }

  return (
    <footer
      aria-label='Status staleness'
      className='border-border bg-background/95 text-muted-foreground mt-auto border-t px-4 py-2 text-xs'
    >
      <p>{content}</p>
    </footer>
  )
}
