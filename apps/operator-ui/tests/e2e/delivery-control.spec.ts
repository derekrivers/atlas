import AxeBuilder from '@axe-core/playwright'
import {
  expect,
  test,
  type Browser,
  type Locator,
  type Page,
} from '@playwright/test'
import {
  E2E_OPERATOR_TOKEN,
  startAtlasApiServer,
} from './atlas-api-server'

const appPort = Number(process.env.ATLAS_OPERATOR_UI_E2E_PORT ?? 4173)
const appBaseURL = `http://127.0.0.1:${appPort}`
const longComponent =
  'operator-ui/admission-explanation-with-an-intentionally-long-responsive-component-name'

let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined

test.beforeAll(async () => {
  apiServer = await startAtlasApiServer()
})

test.afterAll(async () => {
  await apiServer?.stop()
  apiServer = undefined
})

function server() {
  if (!apiServer) throw new Error('live Atlas API server was not started')
  return apiServer
}

async function signIn(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Sign in to delivery control' }).click()
  const dialog = page.getByRole('dialog', { name: 'Operator sign in' })
  await expect(dialog.getByLabel('Bootstrap token')).toBeFocused()
  await dialog.getByLabel('Bootstrap token').fill(E2E_OPERATOR_TOKEN)
  await dialog.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByText('Active Atlas delivery policy', { exact: true })).toBeVisible()
}

async function setPolicyMode(page: Page, mode: 'Draining' | 'Paused' | 'Running') {
  await page.getByLabel('Mode').click()
  await page.getByRole('option', { name: mode, exact: true }).click()
}

async function openPolicyConfirmation(page: Page): Promise<Locator> {
  const trigger = page.getByRole('button', { name: 'Review complete replacement' })
  await trigger.click()
  const dialog = page.getByRole('alertdialog', {
    name: 'Confirm complete policy replacement',
  })
  await expect(dialog).toBeVisible()
  return dialog
}

async function submitPolicy(page: Page): Promise<void> {
  const dialog = await openPolicyConfirmation(page)
  await dialog
    .getByLabel(/I reviewed this complete policy/)
    .check()
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/v1/delivery-control/policy') &&
      response.request().method() === 'POST'
  )
  await dialog.getByRole('button', { name: 'Confirm and submit' }).click()
  const response = await responsePromise
  expect([200, 409]).toContain(response.status())
}

async function newDeliveryPage(browser: Browser): Promise<{
  close: () => Promise<void>
  page: Page
}> {
  const context = await browser.newContext()
  const page = await context.newPage()
  await page.goto(`${appBaseURL}/delivery-control`)
  await signIn(page)
  return { close: () => context.close(), page }
}

async function expectNoHorizontalScrolling(page: Page): Promise<void> {
  const offenders = await page.evaluate(() =>
    [
      document.documentElement,
      document.body,
      ...Array.from(document.querySelectorAll('*')),
    ]
      .filter((element) => {
        const style = window.getComputedStyle(element)
        const box = element.getBoundingClientRect()
        return (
          style.display !== 'none' &&
          style.visibility !== 'hidden' &&
          box.width > 0 &&
          box.height > 0 &&
          (element === document.documentElement ||
            ['auto', 'scroll'].includes(style.overflowX)) &&
          element.scrollWidth > element.clientWidth + 1
        )
      })
      .map((element) => ({
        clientWidth: element.clientWidth,
        element: element.getAttribute('aria-label') ?? element.tagName,
        scrollWidth: element.scrollWidth,
      }))
  )
  expect(offenders).toEqual([])
}

test('live delivery-control policy revisions are explicit, stale-safe, server-authoritative, and locally bounded', async ({
  browser,
  page,
}) => {
  const originalStatuses = server().probeStore().ticket_statuses
  await page.goto('/delivery-control')
  await expect(page.getByRole('heading', { name: 'Delivery control' })).toBeVisible()
  await expect(page.getByText('Operator session required')).toBeVisible()
  await signIn(page)

  await expect(page.getByText('Approved policy ceiling is Atlas policy state')).toBeVisible()
  await expect(page.getByTestId('active-policy-card')).toContainText('Maximum 3')
  await expect(page.getByTestId('active-policy-card')).toContainText(
    'does not report occupied Symphony workers'
  )
  await expect(page.getByTestId('active-policy-card')).toContainText(
    'configured Symphony ceiling is governed separately'
  )
  await expect(page.getByText(longComponent, { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Server decision: Admit')).toBeVisible()
  await expect(page.getByText('Server decision: Hold')).toBeVisible()
  await expect(page.getByText('snapshot_incomplete')).toBeVisible()
  await expect(page.getByText('pagination_gap')).toBeVisible()
  await expect(page.getByText('Server reports indeterminate delivery state')).toBeVisible()
  await expect(page.getByText('write_indeterminate')).toBeVisible()

  const forbiddenLabels = [
    'Live Symphony ceiling',
    'Current Symphony workers',
    'Active workers',
    'Runtime concurrency',
  ]
  for (const forbidden of forbiddenLabels) {
    await expect(page.getByText(forbidden, { exact: true })).toHaveCount(0)
  }

  const second = await newDeliveryPage(browser)
  try {
    await setPolicyMode(page, 'Paused')
    const confirmation = await openPolicyConfirmation(page)
    const summary = confirmation.getByRole('region', {
      name: 'Complete proposed policy summary',
    })
    await expect(summary).toContainText('Paused')
    await expect(summary).toContainText('Approved policy ceiling')
    await expect(summary).toContainText('Working budget')
    await expect(summary).toContainText('Review budget')
    await expect(summary).toContainText('Changes Requested reserve')
    await expect(summary).toContainText('Risk lane limits')
    await expect(summary).toContainText('Component lane limits')
    await expect(summary).toContainText('Expected policy revision')
    await expect(summary).toContainText('1')

    for (let index = 0; index < 6; index += 1) {
      await page.keyboard.press('Tab')
      expect(
        await page.evaluate(() =>
          Boolean(document.activeElement?.closest('[role="alertdialog"]'))
        )
      ).toBe(true)
    }
    await page.keyboard.press('Escape')
    await expect(confirmation).toBeHidden()
    await expect(
      page.getByRole('button', { name: 'Review complete replacement' })
    ).toBeFocused()

    await submitPolicy(page)
    await expect(page.getByRole('status')).toContainText(
      'authoritative policy revision 2'
    )
    await expect(page.getByTestId('active-policy-card')).toContainText('Paused')
    await expect(page.getByTestId('active-policy-card')).toContainText(
      'Already-active work is preserved'
    )

    await setPolicyMode(second.page, 'Draining')
    await submitPolicy(second.page)
    await expect(second.page.getByText('Policy command blocked')).toBeVisible()
    await expect(second.page.getByText('stale_revision')).toBeVisible()
    await expect(second.page.getByText(/entered proposal is preserved/)).toBeVisible()
    await expect(
      second.page.getByRole('button', { name: 'Load and review current policy' })
    ).toBeVisible()

    await second.page
      .getByRole('button', { name: 'Load and review current policy' })
      .click()
    await expect(second.page.getByLabel('Expected policy revision')).toHaveValue('2')
    await setPolicyMode(second.page, 'Draining')
    await submitPolicy(second.page)
    await expect(second.page.getByRole('status')).toContainText(
      'authoritative policy revision 3'
    )
    await expect(second.page.getByTestId('active-policy-card')).toContainText('Draining')
    await expect(second.page.getByTestId('active-policy-card')).toContainText(
      'no new admission occurs while already-active work is preserved'
    )

    await setPolicyMode(second.page, 'Running')
    await submitPolicy(second.page)
    await expect(second.page.getByRole('status')).toContainText(
      'authoritative policy revision 4'
    )
    await expect(second.page.getByTestId('active-policy-card')).toContainText('Running')
  } finally {
    await second.close()
  }

  const probe = server().probeStore()
  expect(probe.policy_revisions).toEqual([
    { approved_symphony_ceiling: 3, integration_budget: 2, mode: 'running', revision: 1, working_budget: 3 },
    { approved_symphony_ceiling: 3, integration_budget: 2, mode: 'paused', revision: 2, working_budget: 3 },
    { approved_symphony_ceiling: 3, integration_budget: 2, mode: 'draining', revision: 3, working_budget: 3 },
    { approved_symphony_ceiling: 3, integration_budget: 2, mode: 'running', revision: 4, working_budget: 3 },
  ])
  expect(probe.ticket_statuses).toEqual(originalStatuses)
  expect(server().externalMutations()).toEqual([])
  const policyReceipts = probe.receipts.filter(
    (item) => item.action === 'delivery_admission_policy.revise'
  )
  expect(policyReceipts).toHaveLength(5)
  expect(
    policyReceipts.filter((item) => item.outcome === 'succeeded')
  ).toHaveLength(4)
  expect(
    policyReceipts.filter((item) => item.outcome === 'conflict')
  ).toHaveLength(1)
})

test('delivery-control dense states, confirmation, keyboard focus, long lanes, and responsive layouts are accessible @accessibility', async ({
  page,
}) => {
  await page.goto('/delivery-control')
  await signIn(page)

  for (const viewport of [
    { height: 768, width: 1366 },
    { height: 768, width: 1024 },
  ]) {
    await page.setViewportSize(viewport)
    const result = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
      .analyze()
    expect(result.violations).toEqual([])
    await expectNoHorizontalScrolling(page)
  }

  const dialog = await openPolicyConfirmation(page)
  await expect(dialog.getByText(longComponent, { exact: true })).toBeVisible()
  const dialogAxe = await new AxeBuilder({ page })
    .include('[role="alertdialog"]')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  expect(dialogAxe.violations).toEqual([])
  for (let index = 0; index < 7; index += 1) {
    await page.keyboard.press('Tab')
    expect(
      await page.evaluate(() =>
        Boolean(document.activeElement?.closest('[role="alertdialog"]'))
      )
    ).toBe(true)
  }
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(
    page.getByRole('button', { name: 'Review complete replacement' })
  ).toBeFocused()
})
