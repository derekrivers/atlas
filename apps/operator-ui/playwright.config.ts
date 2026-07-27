import { defineConfig, devices } from '@playwright/test'

const appPort = 4173
const apiPort = 18000
const appBaseURL = `http://127.0.0.1:${appPort}`
const apiBaseURL = `http://127.0.0.1:${apiPort}`
const databaseURL = 'sqlite:////tmp/atlas-operator-ui-e2e.db'

process.env.ATLAS_OPERATOR_E2E_API_URL = apiBaseURL

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  use: {
    ...devices['Desktop Chrome'],
    baseURL: appBaseURL,
    trace: 'on-first-retry',
  },
  webServer: [
    {
      command: `bash -lc "UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/scratch_seed.py --db ${databaseURL} && UV_CACHE_DIR=/tmp/uv-cache ATLAS_DATABASE_URL=${databaseURL} uv run atlas api serve --host 127.0.0.1 --port ${apiPort}"`,
      cwd: '../..',
      url: `${apiBaseURL}/api/v1/status`,
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: `npm run dev -- --port ${appPort} --strictPort`,
      url: appBaseURL,
      timeout: 120_000,
      reuseExistingServer: false,
    },
  ],
})
