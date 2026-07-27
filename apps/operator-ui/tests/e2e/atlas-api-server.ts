import { spawn, spawnSync, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const repoRoot = join(appRoot, '..', '..')

type AtlasApiServer = {
  apiBaseURL: string
  stop: () => Promise<void>
}

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

export async function startAtlasApiServer(): Promise<AtlasApiServer> {
  const apiBaseURL =
    process.env.ATLAS_OPERATOR_E2E_API_URL ?? 'http://127.0.0.1:18000'
  const apiPort = new URL(apiBaseURL).port
  const tempRoot = mkdtempSync(join(tmpdir(), 'atlas-operator-ui-e2e-'))
  const dbUrl = `sqlite:///${join(tempRoot, 'atlas.db')}`

  runSeedCommand(
    'uv',
    ['run', 'python', 'scripts/scratch_seed.py', '--db', dbUrl],
    dbUrl
  )

  let apiOutput = ''
  const apiProcess = spawn(
    'uv',
    ['run', 'atlas', 'api', 'serve', '--host', '127.0.0.1', '--port', apiPort],
    {
      cwd: repoRoot,
      env: {
        ...process.env,
        ATLAS_DATABASE_URL: dbUrl,
        UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? '/tmp/uv-cache',
        UV_LINK_MODE: process.env.UV_LINK_MODE ?? 'copy',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    }
  )

  apiProcess.stdout?.on('data', (chunk: Buffer) => {
    apiOutput += chunk.toString()
  })
  apiProcess.stderr?.on('data', (chunk: Buffer) => {
    apiOutput += chunk.toString()
  })

  try {
    await waitForApi(apiProcess, apiBaseURL, () => apiOutput)
  } catch (error) {
    await stopProcess(apiProcess)
    rmSync(tempRoot, { force: true, recursive: true })
    throw error
  }

  return {
    apiBaseURL,
    stop: async () => {
      await stopProcess(apiProcess)
      rmSync(tempRoot, { force: true, recursive: true })
    },
  }
}
