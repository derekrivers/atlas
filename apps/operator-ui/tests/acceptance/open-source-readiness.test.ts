import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const repoRoot = join(appRoot, '..', '..')

type OperatorUiPackageJson = {
  private?: unknown
  publishConfig?: unknown
  scripts?: Record<string, string>
}

function readAppFile(path: string): string {
  return readFileSync(join(appRoot, path), 'utf8')
}

function readRepoFile(path: string): string {
  return readFileSync(join(repoRoot, path), 'utf8')
}

function markdownSection(document: string, heading: string): string {
  const start = document.indexOf(heading)
  expect(start, `${heading} should exist`).toBeGreaterThanOrEqual(0)

  const next = document.indexOf('\n## ', start + heading.length)
  return document.slice(start, next === -1 ? undefined : next)
}

describe('Operator UI open-source readiness', () => {
  it('documents a cold-checkout run against a seeded real Atlas API', () => {
    const readme = readAppFile('README.md')
    const runbook = markdownSection(readme, '## Run against a real Atlas store')

    for (const command of [
      'mkdir -p .atlas',
      'export ATLAS_DATABASE_URL="sqlite:///$PWD/.atlas/operator-ui-dev.db"',
      'uv run python -m atlas.tools.operator_ui_e2e_seed --db "$ATLAS_DATABASE_URL"',
      'uv run atlas api serve --enable-writes --host 127.0.0.1 --port 8000',
      'npm --prefix apps/operator-ui ci',
      'VITE_ATLAS_API_BASE_URL=http://127.0.0.1:8000 npm --prefix apps/operator-ui run dev -- --port 4173 --strictPort',
    ]) {
      expect(runbook).toContain(command)
    }

    for (const route of [
      '- `/` - Overview',
      '- `/tickets` - Ticket Board',
      '- `/tickets/ATLAS-1` - Ticket Detail',
      '- `/reviews` - Review Queue',
      '- `/critical-path` - Critical Path',
      '- `/dependency-graph` - Dependency Graph',
      '- `/lessons` - Lessons',
    ]) {
      expect(runbook).toContain(route)
    }

    expect(runbook).toContain('Browser requests stay same-origin under `/api`')
    expect(runbook).toContain('VITE_ATLAS_API_BASE_URL')
  })

  it('states the bounded governed-write contribution boundary', () => {
    const readme = readAppFile('README.md')
    const contributing = markdownSection(
      readme,
      '## Contributing to the Operator UI'
    )
    const prose = contributing.replace(/\s+/g, ' ')

    expect(prose).toContain('permits only authenticated promote/reject')
    for (const forbiddenScope of [
      'lesson editing',
      'merging',
      'ACTIVE archival',
      'generic mutations',
      'Linear writes',
      'GitHub writes',
      'approval controls',
    ]) {
      expect(prose).toContain(forbiddenScope)
    }
    expect(prose).toMatch(
      /memory-only local session,\s+server-owned actor context/
    )
    expect(prose).toContain('stable command-lifecycle idempotency')
  })

  it('records the known contract limits in one named place', () => {
    const readme = readAppFile('README.md')
    const limits = markdownSection(readme, '## Operator UI contract limits')

    expect(limits).toContain('No pagination')
    expect(limits).toContain('No epic on ticket detail')
    expect(limits).toContain('source_ticket_id')
    expect(limits).toContain('related_ticket_ids')
    expect(limits).toContain('Polling, not push')
    expect(limits).toContain('server-sent event')
    expect(limits).toContain('websocket')
    expect(limits).toContain('Refresh loses write authority')
  })

  it('keeps the canonical design doc pointed at the contributor-facing record', () => {
    const operatorUiDoc = readRepoFile('docs/atlas/operator-ui.md')

    expect(operatorUiDoc).toContain('## Open-source contribution boundary')
    expect(operatorUiDoc).toContain('apps/operator-ui/README.md')
    expect(operatorUiDoc).toContain('Operator UI contract limits')
    expect(operatorUiDoc).toContain('THIRD_PARTY_NOTICES.md')
  })

  it('keeps the repository license stable and the UI package non-publishable', () => {
    const repositoryLicense = readRepoFile('LICENSE')
    const packageJson = JSON.parse(
      readAppFile('package.json')
    ) as OperatorUiPackageJson
    const scripts = packageJson.scripts ?? {}

    expect(repositoryLicense).toContain('MIT License')
    expect(repositoryLicense).toContain('Copyright (c) 2026 Derek Rivers')
    expect(packageJson.private).toBe(true)
    expect(packageJson.publishConfig).toBeUndefined()
    expect(scripts.publish).toBeUndefined()
    expect(scripts.prepublish).toBeUndefined()
    expect(scripts.prepublishOnly).toBeUndefined()
    expect(Object.values(scripts).join('\n')).not.toMatch(
      /\b(?:npm|pnpm|yarn)\s+publish\b/
    )
  })
})
