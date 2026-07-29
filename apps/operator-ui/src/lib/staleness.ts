export const STALENESS_THRESHOLD_MS = 30 * 60 * 1000

export type StalenessState = 'fresh' | 'stale' | 'unknown'

export type StalenessSummary = {
  ageLabel: string
  state: StalenessState
  stateLabel: string
  thresholdLabel: string
}

function formatDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000))
  if (seconds < 60) {
    return 'under 1m'
  }

  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) {
    return `${minutes}m`
  }

  const hours = Math.floor(minutes / 60)
  if (hours < 24) {
    return `${hours}h`
  }

  return `${Math.floor(hours / 24)}d`
}

export function stalenessThresholdLabel(
  thresholdMs = STALENESS_THRESHOLD_MS
): string {
  return formatDuration(thresholdMs)
}

export function getStalenessSummary({
  now = Date.now(),
  thresholdMs = STALENESS_THRESHOLD_MS,
  value,
}: {
  now?: number
  thresholdMs?: number
  value: string | null | undefined
}): StalenessSummary {
  const thresholdLabel = stalenessThresholdLabel(thresholdMs)

  if (!value) {
    return {
      ageLabel: 'never',
      state: 'unknown',
      stateLabel: 'No timestamp',
      thresholdLabel,
    }
  }

  const timestamp = new Date(value).getTime()
  if (Number.isNaN(timestamp)) {
    return {
      ageLabel: 'unreadable timestamp',
      state: 'unknown',
      stateLabel: 'Unknown',
      thresholdLabel,
    }
  }

  const ageMs = Math.max(0, now - timestamp)
  const state = ageMs <= thresholdMs ? 'fresh' : 'stale'

  return {
    ageLabel: `${formatDuration(ageMs)} ago`,
    state,
    stateLabel: state === 'fresh' ? 'Fresh' : 'Stale',
    thresholdLabel,
  }
}
