import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

function readProjectFile(path: string): string {
  return readFileSync(join(appRoot, path), 'utf8')
}

function runLocalBin(command: string, args: string[]) {
  return spawnSync(join(appRoot, 'node_modules', '.bin', command), args, {
    cwd: appRoot,
    encoding: 'utf8',
    env: {
      ...process.env,
      ATLAS_RUNNER_BOUNDARY_PROBE: '1',
      NPM_CONFIG_CACHE: process.env.NPM_CONFIG_CACHE ?? '/tmp/npm-cache',
      PLAYWRIGHT_BROWSERS_PATH:
        process.env.PLAYWRIGHT_BROWSERS_PATH ?? '/tmp/ms-playwright',
    },
  })
}

describe('Operator UI runner separation contract', () => {
  it('uses separate scripts and configs for component and end-to-end suites', () => {
    const packageJson = JSON.parse(readProjectFile('package.json')) as {
      scripts: Record<string, string>
    }

    expect(packageJson.scripts['test:browser']).toBe(
      'vitest run --config vitest.browser.config.ts'
    )
    expect(packageJson.scripts['test:e2e']).toBe(
      'playwright test --config playwright.config.ts --grep-invert @accessibility'
    )
    expect(packageJson.scripts['test:a11y']).toBe(
      'playwright test --config playwright.config.ts --grep @accessibility'
    )

    expect(readProjectFile('vitest.browser.config.ts')).toContain(
      "include: ['tests/component/**/*.test.tsx']"
    )
    expect(readProjectFile('vitest.browser.config.ts')).toContain(
      "from '@vitest/browser-playwright'"
    )
    expect(readProjectFile('playwright.config.ts')).toContain(
      "testDir: './tests/e2e'"
    )
    expect(readProjectFile('playwright.config.ts')).toContain(
      "from '@playwright/test'"
    )
  })

  it(
    'fails when an end-to-end spec is invoked through Vitest',
    () => {
      const result = runLocalBin('vitest', [
        'run',
        '--config',
        'vitest.config.ts',
        'tests/e2e/app-shell.spec.ts',
      ])
      const output = result.stdout + result.stderr

      expect(result.status).not.toBe(0)
      expect(output).toMatch(/Playwright Test|No test files found/)
    },
    30_000
  )

  it(
    'fails when a Vitest browser component spec is invoked through Playwright',
    () => {
      const result = runLocalBin('playwright', [
        'test',
        '--config',
        'playwright.config.ts',
        'tests/component/operator-shell.browser.test.tsx',
        '--list',
      ])
      const output = result.stdout + result.stderr

      expect(result.status).not.toBe(0)
      expect(output).toMatch(/No tests found|Cannot find package|Error/)
    },
    30_000
  )
})
