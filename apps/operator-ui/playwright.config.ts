import { defineConfig, devices } from '@playwright/test'

const appPort = Number(process.env.ATLAS_OPERATOR_UI_E2E_PORT ?? 4173)
const apiPort = Number(process.env.ATLAS_OPERATOR_API_E2E_PORT ?? 18000)
const appBaseURL = `http://127.0.0.1:${appPort}`
const apiBaseURL = `http://127.0.0.1:${apiPort}`

process.env.ATLAS_OPERATOR_E2E_API_URL = apiBaseURL
process.env.VITE_ATLAS_API_BASE_URL = apiBaseURL

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  timeout: 60_000,
  expect: {
    timeout: 5_000,
  },
  use: {
    ...devices['Desktop Chrome'],
    baseURL: appBaseURL,
    screenshot: 'off',
    trace: 'off',
    video: 'off',
  },
  webServer: {
    command: `npm run build:bundle && ./node_modules/.bin/vite preview --host 127.0.0.1 --port ${appPort} --strictPort`,
    env: {
      VITE_ATLAS_API_BASE_URL: apiBaseURL,
    },
    reuseExistingServer: false,
    timeout: 120_000,
    url: appBaseURL,
  },
})
