import { spawnSync } from 'node:child_process'
import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
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
  const probeRoot = join(appRoot, 'src', 'features', '__lint_probe__')
  rmSync(probeRoot, { force: true, recursive: true })
  mkdirSync(probeRoot, { recursive: true })

  try {
    for (const [file, source] of Object.entries(files)) {
      writeFileSync(join(probeRoot, file), source)
    }

    return spawnSync(
      'npx',
      ['eslint', 'src/features/__lint_probe__'],
      {
        cwd: appRoot,
        encoding: 'utf8',
      }
    )
  } finally {
    rmSync(probeRoot, { force: true, recursive: true })
  }
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
    'rejects ad-hoc view state imports outside the shared primitives',
    () => {
      const result = runEslintProbe({
        'ad-hoc-states.tsx':
          "export function LoadingState() { return <div>Loading</div> }\n",
        'view.tsx':
          "import { LoadingState } from './ad-hoc-states'\nexport function View() { return <LoadingState /> }\n",
      })

      expect(result.status).toBe(1)
      expect(result.stderr + result.stdout).toContain(
        'View state primitives must be imported from @/components/states'
      )
    },
    20_000
  )

  it(
    'rejects view-local polling overrides',
    () => {
      const result = runEslintProbe({
        'view.tsx':
          "export function View() { return null }\nexport const options = { refetchInterval: 1000 }\n",
      })

      expect(result.status).toBe(1)
      expect(result.stderr + result.stdout).toContain(
        'View files must use the shared Atlas query polling policy instead of setting refetchInterval'
      )
    },
    20_000
  )
})
