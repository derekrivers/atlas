import { spawn, spawnSync, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const repoRoot = join(appRoot, '..', '..')
const atlasServeArgs = ['run', 'atlas', 'api', 'serve']

export type AtlasStoreProbe = {
  context_lesson_ids: string[]
  lessons: Record<string, { confidence: number | null; status: string; updated_at: string }>
  receipts: Array<{
    action: string
    actor: { id: string; type: string }
    idempotency_key_identity: string
    outcome: string
    result_code: string
    target: { id: string; type: string }
  }>
}

export type AtlasApiServer = {
  apiBaseURL: string
  launchMode: () => 'production-cli' | 'test-factory'
  output: () => string
  probeStore: () => AtlasStoreProbe
  restart: (options?: StartAtlasApiServerOptions) => Promise<void>
  runCli: (args: string[]) => { status: number | null; stderr: string; stdout: string }
  setAcceptanceState: (state: AcceptanceGitHubState) => void
  setClock: (timestamp: string) => void
  stop: () => Promise<void>
}

export type StartAtlasApiServerOptions = {
  acceptance?: boolean
  clock?: string | null
  receiptFailure?: boolean
  receiptFailureCanary?: string
  seedPath?: string
}

export type AcceptanceGitHubState = {
  mode: 'current' | 'failure' | 'head-moved' | 'main-moved' | 'timeout'
}

export const E2E_OPERATOR_TOKEN =
  'atlas-operator-e2e-token-0123456789ABCDEFGHJKLMNPQRSTxyz!@#'

function runSeedCommand(command: string, args: string[], dbUrl: string): void {
  const result = spawnSync(command, args, {
    cwd: repoRoot,
    encoding: 'utf8',
    env: {
      ...process.env,
      ATLAS_DATABASE_URL: dbUrl,
      UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? '/tmp/uv-cache',
      UV_LINK_MODE: process.env.UV_LINK_MODE ?? 'copy',
    },
  })

  if (result.status !== 0) {
    throw new Error(
      [
        `${command} ${args.join(' ')} failed with ${result.status}`,
        result.stdout,
        result.stderr,
      ].join('\n')
    )
  }
}

async function delay(ms: number): Promise<void> {
  await new Promise((resolve) => {
    setTimeout(resolve, ms)
  })
}

async function waitForApi(
  apiProcess: ChildProcess,
  apiBaseURL: string,
  apiOutput: () => string
): Promise<void> {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (apiProcess.exitCode !== null) {
      throw new Error(`atlas api serve exited early:\n${apiOutput()}`)
    }

    try {
      const response = await fetch(`${apiBaseURL}/api/v1/status`)
      if (response.ok) {
        return
      }
    } catch (_error) {
      // The API may not be listening yet; retry below.
    }
    await delay(250)
  }

  throw new Error(`atlas api serve did not become ready:\n${apiOutput()}`)
}

async function stopProcess(apiProcess: ChildProcess): Promise<void> {
  if (apiProcess.exitCode !== null) {
    return
  }

  const exited = new Promise<void>((resolve) => {
    apiProcess.once('exit', () => resolve())
  })

  apiProcess.kill('SIGTERM')
  await Promise.race([exited, delay(5_000)])

  if (apiProcess.exitCode === null) {
    apiProcess.kill('SIGKILL')
    await exited
  }
}

export async function startAtlasApiServer({
  acceptance = false,
  clock,
  receiptFailure = false,
  receiptFailureCanary = 'seeded-receipt-failure',
  seedPath,
}: StartAtlasApiServerOptions = {}): Promise<AtlasApiServer> {
  const apiBaseURL =
    process.env.ATLAS_OPERATOR_E2E_API_URL ?? 'http://127.0.0.1:18000'
  const apiPort = new URL(apiBaseURL).port
  const tempRoot = mkdtempSync(join(tmpdir(), 'atlas-operator-ui-e2e-'))
  const dbUrl = `sqlite:///${join(tempRoot, 'atlas.db')}`
  const clockPath = join(tempRoot, 'clock.txt')
  const acceptanceStatePath = join(tempRoot, 'acceptance-github-state.json')
  writeFileSync(clockPath, clock ?? '2026-08-11T12:00:00+00:00', 'utf8')
  writeFileSync(acceptanceStatePath, JSON.stringify({ mode: 'current' }), 'utf8')

  const seedArgs = [
    'run',
    'python',
    '-m',
    'atlas.tools.operator_ui_e2e_seed',
    '--db',
    dbUrl,
  ]
  if (seedPath) {
    seedArgs.push('--seed', seedPath)
  }

  runSeedCommand('uv', seedArgs, dbUrl)

  let apiOutput = ''
  let apiProcess: ChildProcess
  let launchMode: 'production-cli' | 'test-factory' = 'production-cli'
  let currentOptions: StartAtlasApiServerOptions = {
    acceptance,
    clock,
    receiptFailure,
    receiptFailureCanary,
    seedPath,
  }

  function spawnApi(): ChildProcess {
    launchMode =
      currentOptions.acceptance || currentOptions.clock || currentOptions.receiptFailure
        ? 'test-factory'
        : 'production-cli'
    const args = launchMode === 'test-factory'
      ? [
          'run',
          'uvicorn',
          '--app-dir',
          join(appRoot, 'tests', 'e2e'),
          'atlas_api_app:app',
          '--host',
          '127.0.0.1',
          '--port',
          apiPort,
        ]
      : [
          ...atlasServeArgs,
          '--enable-writes',
          '--host',
          '127.0.0.1',
          '--port',
          apiPort,
        ]
    const child = spawn('uv', args, {
      cwd: repoRoot,
      env: {
        ...process.env,
        ATLAS_DATABASE_URL: dbUrl,
        ATLAS_E2E_CLOCK_FILE: clockPath,
        ATLAS_E2E_ACCEPTANCE: currentOptions.acceptance ? '1' : '0',
        ATLAS_E2E_ACCEPTANCE_STATE_FILE: acceptanceStatePath,
        ATLAS_E2E_RECEIPT_FAILURE: currentOptions.receiptFailure ? '1' : '0',
        ATLAS_E2E_RECEIPT_FAILURE_CANARY:
          currentOptions.receiptFailureCanary ?? 'seeded-receipt-failure',
        ATLAS_OPERATOR_TOKEN: E2E_OPERATOR_TOKEN,
        UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? '/tmp/uv-cache',
        UV_LINK_MODE: process.env.UV_LINK_MODE ?? 'copy',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    child.stdout?.on('data', (chunk: Buffer) => {
      apiOutput += chunk.toString()
    })
    child.stderr?.on('data', (chunk: Buffer) => {
      apiOutput += chunk.toString()
    })
    return child
  }

  apiProcess = spawnApi()

  try {
    await waitForApi(apiProcess, apiBaseURL, () => apiOutput)
  } catch (error) {
    await stopProcess(apiProcess)
    rmSync(tempRoot, { force: true, recursive: true })
    throw error
  }

  return {
    apiBaseURL,
    launchMode: () => launchMode,
    output: () => apiOutput,
    probeStore: () => {
      const result = spawnSync(
        'uv',
        [
          'run',
          'python',
          '-m',
          'atlas.tools.operator_ui_e2e_probe',
          '--db',
          dbUrl,
        ],
        {
          cwd: repoRoot,
          encoding: 'utf8',
          env: {
            ...process.env,
            UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? '/tmp/uv-cache',
            UV_LINK_MODE: process.env.UV_LINK_MODE ?? 'copy',
          },
        }
      )
      if (result.status !== 0) {
        throw new Error(`store probe failed:\n${result.stdout}\n${result.stderr}`)
      }
      return JSON.parse(result.stdout) as AtlasStoreProbe
    },
    restart: async (options = {}) => {
      await stopProcess(apiProcess)
      currentOptions = { ...currentOptions, ...options }
      if (typeof options.clock === 'string') {
        writeFileSync(clockPath, options.clock, 'utf8')
      }
      apiProcess = spawnApi()
      await waitForApi(apiProcess, apiBaseURL, () => apiOutput)
    },
    runCli: (args) => {
      const result = spawnSync('uv', ['run', 'atlas', ...args], {
        cwd: repoRoot,
        encoding: 'utf8',
        env: {
          ...process.env,
          ATLAS_DATABASE_URL: dbUrl,
          UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? '/tmp/uv-cache',
          UV_LINK_MODE: process.env.UV_LINK_MODE ?? 'copy',
        },
      })
      return {
        status: result.status,
        stderr: result.stderr,
        stdout: result.stdout,
      }
    },
    setAcceptanceState: (state) =>
      writeFileSync(acceptanceStatePath, JSON.stringify(state), 'utf8'),
    setClock: (timestamp) => writeFileSync(clockPath, timestamp, 'utf8'),
    stop: async () => {
      await stopProcess(apiProcess)
      rmSync(tempRoot, { force: true, recursive: true })
    },
  }
}
