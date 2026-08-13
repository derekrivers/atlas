import { spawn, spawnSync, type ChildProcess } from 'node:child_process'
import {
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const repoRoot = join(appRoot, '..', '..')
const atlasServeArgs = ['run', 'atlas', 'api', 'serve']

export type AtlasStoreProbe = {
  acceptance_sessions: Array<{
    id: string
    lifecycle: string
    stored_merge_ready: boolean
    step_summaries: Record<
      string,
      {
        reasons: string[]
        receipt_ids: string[]
        state: string
        verification?: {
          head_commit: string | null
          status: string
          verdict_id: string
        } | null
      }
    >
    updated_at: string
  }>
  context_lesson_ids: string[]
  evidence: Array<{
    commit_sha: string | null
    created_by_type: string
    id: string
    status: string
    ticket_id: string
    type: string
  }>
  lessons: Record<string, { confidence: number | null; status: string; updated_at: string }>
  pm_sync_receipts: Array<{ id: string; result: string }>
  receipts: Array<{
    action: string
    actor: { id: string; type: string }
    idempotency_key_identity: string
    outcome: string
    result_code: string
    target: { id: string; type: string }
  }>
  schema: { revision: string | null; tables: string[] }
  ticket_statuses: Record<string, string>
  ticket_transitions: Array<{
    from: string
    id: string
    ticket_id: string
    to: string
  }>
  verification_checks: Array<{
    id: string
    required: boolean
    status: string
    ticket_id: string
    type: string
  }>
}

export type ExternalMutationEvent = {
  category: string
  operation: string
}

export type AtlasApiServer = {
  apiBaseURL: string
  externalMutations: () => ExternalMutationEvent[]
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
  delay_ms?: number
  error_canary?: string
  github?:
    | 'current'
    | 'evidence-malformed'
    | 'failure'
    | 'head-moved'
    | 'head-moved-after-evidence'
    | 'main-moved'
    | 'main-moved-after-evidence'
    | 'malformed'
    | 'timeout'
  mode?: 'current' | 'failure' | 'head-moved' | 'main-moved' | 'timeout'
  receipt_failure_action?:
    | 'acceptance_session.confirm'
    | 'acceptance_session.pull_evidence'
    | 'acceptance_session.verify'
  store_failure?: boolean
  ticket?: 'criteria-drift' | 'current' | 'missing'
  verification?:
    | 'canonical'
    | 'close-set-mismatch'
    | 'failed'
    | 'malformed'
    | 'not_applicable'
    | 'old-head'
    | 'pending'
    | 'warning'
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
  const externalEventsPath = join(tempRoot, 'external-mutation-events.jsonl')
  let stateRevision = 0
  writeFileSync(clockPath, clock ?? '2026-08-11T12:00:00+00:00', 'utf8')
  writeFileSync(
    acceptanceStatePath,
    JSON.stringify({ github: 'current', revision: stateRevision }),
    'utf8'
  )
  writeFileSync(externalEventsPath, '', 'utf8')

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
        ATLAS_E2E_EXTERNAL_EVENTS_FILE: externalEventsPath,
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
    externalMutations: () =>
      readFileSync(externalEventsPath, 'utf8')
        .split('\n')
        .filter(Boolean)
        .map((line) => JSON.parse(line) as ExternalMutationEvent),
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
    setAcceptanceState: (state) => {
      stateRevision += 1
      const nextStatePath = `${acceptanceStatePath}.next`
      writeFileSync(
        nextStatePath,
        JSON.stringify({ ...state, revision: stateRevision }),
        'utf8'
      )
      renameSync(nextStatePath, acceptanceStatePath)
    },
    setClock: (timestamp) => writeFileSync(clockPath, timestamp, 'utf8'),
    stop: async () => {
      await stopProcess(apiProcess)
      rmSync(tempRoot, { force: true, recursive: true })
    },
  }
}
