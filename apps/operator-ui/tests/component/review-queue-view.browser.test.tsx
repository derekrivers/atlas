import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import { atlasOpenApiEnums } from '@/api/atlas-openapi-runtime'
import type { components } from '@/api/atlas-openapi'
import { ReviewChecksMatrix } from '@/features/reviews/review-queue-view'

type ReviewCheck = components['schemas']['ReviewCheckSchema']

let mountedRoot: Root | undefined
let container: HTMLDivElement | undefined

async function render(checks: readonly ReviewCheck[]) {
  container = document.createElement('div')
  document.body.append(container)
  mountedRoot = createRoot(container)

  await act(async () => {
    mountedRoot?.render(<ReviewChecksMatrix checks={checks} />)
  })
}

function checkRow(checkType: string): HTMLElement {
  const row = document.querySelector(`[data-check-type="${checkType}"]`)
  if (!(row instanceof HTMLElement)) {
    throw new Error(`Missing check row: ${checkType}`)
  }
  return row
}

afterEach(() => {
  mountedRoot?.unmount()
  container?.remove()
  mountedRoot = undefined
  container = undefined
})

describe('review queue check matrix', () => {
  it('renders passed, not-applicable, and never-run checks distinctly', async () => {
    await render([
      {
        check_type: 'tests',
        status: 'passed',
      },
      {
        check_type: 'documentation',
        status: 'not_applicable',
      },
    ])

    expect(document.querySelectorAll('[data-testid="review-check-row"]')).toHaveLength(
      atlasOpenApiEnums.VerificationCheckType.length
    )

    const passed = checkRow('tests')
    const notApplicable = checkRow('documentation')
    const neverRun = checkRow('lint')

    expect(passed.dataset.checkState).toBe('passed')
    expect(passed.textContent).toContain('Passed')
    expect(notApplicable.dataset.checkState).toBe('not_applicable')
    expect(notApplicable.textContent).toContain('Not applicable')
    expect(neverRun.dataset.checkState).toBe('not_run')
    expect(neverRun.textContent).toContain('Never run')
  })
})
