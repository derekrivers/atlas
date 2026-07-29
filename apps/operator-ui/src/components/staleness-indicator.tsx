import { AlertTriangle, CheckCircle2, CircleDashed } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import {
  getStalenessSummary,
  type StalenessState,
} from '@/lib/staleness'
import { cn } from '@/lib/utils'

type StalenessIndicatorProps = {
  className?: string
  label: string
  now?: number
  testId?: string
  value: string | null | undefined
}

const stateClassNames = {
  fresh: 'border-primary/40 text-primary',
  stale: 'border-destructive/40 text-destructive',
  unknown: 'border-border text-muted-foreground',
} satisfies Record<StalenessState, string>

const stateIcons = {
  fresh: CheckCircle2,
  stale: AlertTriangle,
  unknown: CircleDashed,
} satisfies Record<StalenessState, React.ElementType>

export function StalenessIndicator({
  className,
  label,
  now,
  testId,
  value,
}: StalenessIndicatorProps) {
  const summary = getStalenessSummary({ now, value })
  const Icon = stateIcons[summary.state]

  return (
    <div
      data-staleness-state={summary.state}
      data-testid={testId}
      className={cn('flex flex-wrap items-center gap-2 text-sm', className)}
    >
      <span className='text-muted-foreground font-medium'>{label}:</span>
      <Badge
        variant='outline'
        className={cn('gap-1.5', stateClassNames[summary.state])}
      >
        <Icon aria-hidden='true' className='size-3' />
        {summary.stateLabel}
      </Badge>
      <span className='font-mono tabular-nums'>{summary.ageLabel}</span>
      <span className='text-muted-foreground'>
        threshold {summary.thresholdLabel}
      </span>
    </div>
  )
}
