import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import type { ProxyOptions, UserConfig } from 'vite'
import viteConfig from '../../vite.config'
import { ATLAS_QUERY_POLL_INTERVAL_MS } from '@/api/query-policy'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

function apiProxy(): ProxyOptions {
  const config = viteConfig as UserConfig
  const proxy = config.server?.proxy

  if (!proxy || typeof proxy === 'string' || Array.isArray(proxy)) {
    throw new Error('Vite proxy config is not object-shaped')
  }

  const entry = proxy['/api']
  if (typeof entry !== 'object') {
    throw new Error('Vite /api proxy config is not object-shaped')
  }

  return entry
}

function runEslintProbe(files: Record<string, string>) {
  const tempRoot = mkdtempSync(join(tmpdir(), 'atlas-eslint-probe-'))
  const probeRoot = join(tempRoot, 'src', 'features', 'probe')
  mkdirSync(probeRoot, { recursive: true })

  for (const [file, source] of Object.entries(files)) {
    writeFileSync(join(probeRoot, file), source)
  }
  const result = spawnSync(
    join(appRoot, 'node_modules', '.bin', 'eslint'),
    ['--config', join(appRoot, 'eslint.config.js'), 'src/features/probe'],
    {
      cwd: tempRoot,
      encoding: 'utf8',
    }
  )
  rmSync(tempRoot, { force: true, recursive: true })
  return result
}

function expectLintProbeFailure(
  files: Record<string, string>,
  expectedMessage: string
) {
  const result = runEslintProbe(files)

  expect(result.status).toBe(1)
  expect(result.stderr + result.stdout).toContain(expectedMessage)
}

describe('Atlas query layer configuration', () => {
  it('proxies same-origin API calls to the loopback API by default', () => {
    expect(apiProxy()).toMatchObject({
      changeOrigin: false,
      target: 'http://127.0.0.1:8000',
    })
  })

  it('documents one shared polling interval for query consumers', () => {
    expect(ATLAS_QUERY_POLL_INTERVAL_MS).toBe(30_000)
  })

  it(
    'rejects query-backed feature views without shared state primitives',
    () => {
      const expectedMessage =
        'Feature views that use Atlas query hooks must import shared state primitives from @/components/states'

      expectLintProbeFailure(
        {
          'view.tsx':
            "import { useTicketsQuery } from '@/api/query-hooks'\nfunction Spinner() { return <div>Loading</div> }\nexport function View() { const query = useTicketsQuery(); return query.isLoading ? <Spinner /> : null }\n",
        },
        expectedMessage
      )
      expectLintProbeFailure(
        {
          'spinner.tsx':
            "export function Spinner() { return <div>Loading</div> }\n",
          'view.tsx':
            "import { useTicketsQuery } from '@/api/query-hooks'\nimport { Spinner } from './spinner'\nexport function View() { const query = useTicketsQuery(); return query.isLoading ? <Spinner /> : null }\n",
        },
        expectedMessage
      )
      expectLintProbeFailure(
        {
          'view.tsx':
            "import { useQuery } from '@tanstack/react-query'\nfunction Spinner() { return <div>Loading</div> }\nexport function View() { const query = useQuery({ queryKey: ['probe'], queryFn: () => 'ok' }); return query.isLoading ? <Spinner /> : null }\n",
        },
        expectedMessage
      )
    },
    40_000
  )

  it(
    'rejects ad-hoc view state imports outside the shared primitives',
    () => {
      expectLintProbeFailure(
        {
          'ad-hoc-states.tsx':
            "export function LoadingState() { return <div>Loading</div> }\n",
          'view.tsx':
            "import { LoadingState } from './ad-hoc-states'\nexport function View() { return <LoadingState /> }\n",
        },
        'View state primitives must be imported from @/components/states'
      )
    },
    20_000
  )

  it(
    'rejects view-local polling overrides',
    () => {
      const expectedMessage =
        'View files must use the shared Atlas query polling policy instead of setting refetchInterval or refetchIntervalInBackground'

      expectLintProbeFailure(
        {
          'view.tsx':
            "export function View() { return null }\nexport const options = { refetchInterval: 1000 }\n",
        },
        expectedMessage
      )
      expectLintProbeFailure(
        {
          'view.tsx':
            "export function View() { return null }\nexport const options = { refetchIntervalInBackground: true }\n",
        },
        expectedMessage
      )
    },
    20_000
  )
})
