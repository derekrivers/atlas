import { expect, test, type Browser, type Page } from '@playwright/test'
import { startAtlasApiServer } from './atlas-api-server'

let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined

test.beforeAll(async () => {
  apiServer = await startAtlasApiServer()
})

test.afterAll(async () => {
  await apiServer?.stop()
  apiServer = undefined
})

async function boardKeys(page: Page): Promise<string[]> {
  return page
    .getByTestId('ticket-board-row')
    .locator('td:first-child')
    .allInnerTexts()
}

async function firstBoardRowCells(page: Page): Promise<string[]> {
  return page
    .getByTestId('ticket-board-row')
    .first()
    .locator('td')
    .allInnerTexts()
}

async function openColdPage(browser: Browser, url: string): Promise<Page> {
  const page = await browser.newPage()
  await page.goto(url)
  await expect(page.getByRole('heading', { name: 'Ticket Board' })).toBeVisible()
  return page
}

test('renders board projection fields and detail links against the seeded live API', async ({
  page,
}) => {
  await page.goto('/tickets')

  const row = page.getByTestId('ticket-board-row').filter({ hasText: 'ATLAS-2' })
  await expect(row).toBeVisible()
  await expect(row.getByRole('link', { name: 'ATLAS-2' })).toHaveAttribute(
    'href',
    '/tickets/ATLAS-2'
  )

  const cells = await row.locator('td').allInnerTexts()
  expect(cells).toEqual([
    'ATLAS-2',
    'Seeded ATLAS-2',
    'In Progress',
    'Feature',
    '2',
    'High',
  ])
})

test('excludes terminal tickets on first load and reveals them in one interaction', async ({
  page,
}) => {
  await page.goto('/tickets')

  await expect(page.getByTestId('ticket-board-row')).toHaveCount(1)
  expect(await boardKeys(page)).toEqual(['ATLAS-2'])

  await page.getByRole('button', { name: /Show terminal/ }).click()

  await expect(page.getByTestId('ticket-board-row')).toHaveCount(17)
  expect(await boardKeys(page)).toContain('ATLAS-1')
  expect(await boardKeys(page)).toContain('ATLAS-4')
})

test('sorts ticket keys by their numeric segment', async ({ page }) => {
  await page.goto('/tickets')
  await page.getByRole('button', { name: /Show terminal/ }).click()

  await expect(page.getByTestId('ticket-board-row')).toHaveCount(17)
  const keys = await boardKeys(page)
  expect(
    keys.filter((key) =>
      ['ATLAS-1', 'ATLAS-2', 'ATLAS-10', 'ATLAS-100'].includes(key)
    )
  ).toEqual(['ATLAS-1', 'ATLAS-2', 'ATLAS-10', 'ATLAS-100'])
})

test('round-trips filter and sort state through a copied URL', async ({
  browser,
  page,
}) => {
  await page.goto('/tickets')

  await page.getByRole('button', { name: 'Filter status' }).click()
  await page.getByRole('menuitemcheckbox', { name: 'Done' }).click()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: /Priority/ }).click()
  await page.getByRole('button', { name: /Priority/ }).click()

  await expect(page).toHaveURL(/status=done/)
  await expect(page).toHaveURL(/terminal=show/)
  await expect(page).toHaveURL(/sort=priority\.desc/)
  expect((await firstBoardRowCells(page))[0]).toBe('ATLAS-100')

  const copiedUrl = page.url()
  const coldPage = await openColdPage(browser, copiedUrl)
  try {
    await expect(coldPage).toHaveURL(/status=done/)
    await expect(coldPage).toHaveURL(/terminal=show/)
    await expect(coldPage).toHaveURL(/sort=priority\.desc/)
    expect((await firstBoardRowCells(coldPage))[0]).toBe('ATLAS-100')
    expect(await boardKeys(coldPage)).not.toContain('ATLAS-4')
  } finally {
    await coldPage.close()
  }
})

test('issues exactly one board request per page load', async ({ page }) => {
  let boardRequests = 0

  await page.route('**/*', async (route) => {
    const requestUrl = new URL(route.request().url())
    if (
      requestUrl.pathname === '/api/v1/tickets' &&
      requestUrl.search === ''
    ) {
      boardRequests += 1
    }
    await route.continue()
  })

  await page.goto('/tickets')
  await expect(page.getByTestId('ticket-board-row')).toHaveCount(1)
  expect(boardRequests).toBe(1)

  await page.getByLabel('Search tickets').fill('ATLAS-2')
  await page.getByRole('button', { name: /Show terminal/ }).click()
  await expect(page.getByTestId('ticket-board-row')).toHaveCount(1)
  expect(boardRequests).toBe(1)
})
