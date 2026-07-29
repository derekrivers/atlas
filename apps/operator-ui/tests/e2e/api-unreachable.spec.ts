import { expect, test } from '@playwright/test'
import { startAtlasApiServer } from './atlas-api-server'

let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined

test.beforeAll(async () => {
  apiServer = await startAtlasApiServer()
})

test.afterAll(async () => {
  await apiServer?.stop()
  apiServer = undefined
})

test('renders the named API-unreachable state after the API process stops', async ({
  page,
}) => {
  if (!apiServer) {
    throw new Error('Atlas API e2e server was not started')
  }

  const apiBaseURL = apiServer.apiBaseURL
  await apiServer.stop()
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'API unreachable' })).toBeVisible()
  await expect(page.getByText(apiBaseURL)).toBeVisible()
  await expect(page.getByText('atlas api serve')).toBeVisible()
})
