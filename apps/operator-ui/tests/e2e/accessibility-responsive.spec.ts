import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Locator, type Page } from '@playwright/test'
import {
  OPERATOR_TICKET_DETAIL_PLACEHOLDER_KEY,
  operatorSurfaces,
  type OperatorSurface,
} from '../../src/app-shell/surfaces'
import { E2E_OPERATOR_TOKEN, startAtlasApiServer } from './atlas-api-server'

const appPort = Number(process.env.ATLAS_OPERATOR_UI_E2E_PORT ?? 4173)
const appBaseURL = `http://127.0.0.1:${appPort}`
const seededCriticalPathHead = 'ATLAS-2'
const axeTags = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']
const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[role="button"]:not([aria-disabled="true"])',
  '[role="link"]:not([aria-disabled="true"])',
  '[role="menuitem"]:not([aria-disabled="true"])',
  '[role="menuitemcheckbox"]:not([aria-disabled="true"])',
  '[role="tab"]:not([aria-disabled="true"])',
  '[data-slot="command-item"]:not([aria-disabled="true"])',
].join(',')

type ColorMode = 'dark' | 'light'
type AxeViolation = Awaited<ReturnType<AxeBuilder['analyze']>>['violations'][number]
type DeliveredView = {
  heading: string
  id: OperatorSurface['id']
  name: string
  path: string
}

const colorModes = ['light', 'dark'] as const satisfies readonly ColorMode[]
const responsiveViewports = [
  { height: 768, name: 'laptop', width: 1366 },
  { height: 768, name: 'tablet', width: 1024 },
] as const

const deliveredViews: readonly DeliveredView[] = operatorSurfaces.map((surface) => ({
  heading:
    surface.id === 'ticket-detail'
      ? `Seeded ${OPERATOR_TICKET_DETAIL_PLACEHOLDER_KEY}`
      : surface.placeholder.title,
  id: surface.id,
  name: surface.title,
  path: surface.href,
}))

let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined

test.beforeAll(async () => {
  apiServer = await startAtlasApiServer()
})

test.afterAll(async () => {
  await apiServer?.stop()
  apiServer = undefined
})

function axeSummary(violations: readonly AxeViolation[]): string {
  return violations
    .map((violation) => {
      const targets = violation.nodes
        .map((node) => node.target.join(' '))
        .join('; ')
      return `${violation.id}: ${violation.help} (${targets})`
    })
    .join('\n')
}

async function setColorMode(page: Page, mode: ColorMode): Promise<void> {
  await page.context().addCookies([
    {
      name: 'vite-ui-theme',
      url: appBaseURL,
      value: mode,
    },
  ])
}

async function openView(
  page: Page,
  view: DeliveredView,
  mode: ColorMode = 'light'
): Promise<void> {
  await setColorMode(page, mode)
  await page.goto(view.path)
  await expect(page.locator('html')).toHaveClass(new RegExp(`\\b${mode}\\b`))
  await expect(
    page.getByRole('heading', { exact: true, name: view.heading })
  ).toBeVisible()
}

async function axeViolations(page: Page): Promise<readonly AxeViolation[]> {
  const result = await new AxeBuilder({ page }).withTags([...axeTags]).analyze()
  return result.violations
}

async function blurActiveElement(page: Page): Promise<void> {
  await page.evaluate(() => {
    const activeElement = document.activeElement
    if (
      activeElement instanceof HTMLElement ||
      activeElement instanceof SVGElement
    ) {
      activeElement.blur()
    }
  })
}

async function focusableSnapshots(page: Page): Promise<
  {
    index: number
    label: string
  }[]
> {
  return page.evaluate((selector) => {
    function isVisible(element: Element): boolean {
      const style = window.getComputedStyle(element)
      const box = element.getBoundingClientRect()
      return (
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        box.width > 0 &&
        box.height > 0 &&
        !element.closest('[hidden], [inert]')
      )
    }

    function labelFor(element: Element): string {
      const text = element.textContent?.replace(/\s+/g, ' ').trim() ?? ''
      return [
        element.tagName.toLowerCase(),
        element.getAttribute('role') ?? '',
        element.getAttribute('aria-label') ?? '',
        text,
        element.getAttribute('href') ?? '',
      ]
        .filter(Boolean)
        .join(' ')
    }

    return Array.from(document.querySelectorAll(selector))
      .filter((element) => {
        const tabIndex = (element as HTMLElement | SVGElement).tabIndex
        const disabled =
          'disabled' in element && Boolean((element as HTMLButtonElement).disabled)
        return tabIndex >= 0 && !disabled && isVisible(element)
      })
      .map((element, index) => ({ index, label: labelFor(element) }))
  }, focusableSelector)
}

async function activeFocusableState(page: Page): Promise<{
  hasVisibleFocus: boolean
  index: number
  label: string
} | null> {
  return page.evaluate((selector) => {
    function isVisible(element: Element): boolean {
      const style = window.getComputedStyle(element)
      const box = element.getBoundingClientRect()
      return (
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        box.width > 0 &&
        box.height > 0 &&
        !element.closest('[hidden], [inert]')
      )
    }

    function labelFor(element: Element): string {
      const text = element.textContent?.replace(/\s+/g, ' ').trim() ?? ''
      return [
        element.tagName.toLowerCase(),
        element.getAttribute('role') ?? '',
        element.getAttribute('aria-label') ?? '',
        text,
        element.getAttribute('href') ?? '',
      ]
        .filter(Boolean)
        .join(' ')
    }

    const elements = Array.from(document.querySelectorAll(selector)).filter(
      (element) => {
        const tabIndex = (element as HTMLElement | SVGElement).tabIndex
        const disabled =
          'disabled' in element && Boolean((element as HTMLButtonElement).disabled)
        return tabIndex >= 0 && !disabled && isVisible(element)
      }
    )
    const activeElement = document.activeElement
    if (!activeElement || activeElement === document.body) {
      return null
    }

    const index = elements.indexOf(activeElement)
    if (index === -1) {
      return null
    }

    const style = window.getComputedStyle(activeElement)
    const outlineWidth = Number.parseFloat(style.outlineWidth)
    const svgFrame = activeElement.querySelector(
      '[data-testid="dependency-graph-node-frame"]'
    )
    const svgFrameStyle = svgFrame ? window.getComputedStyle(svgFrame) : null
    const hasVisibleFocus =
      activeElement.matches(':focus-visible') &&
      ((style.outlineStyle !== 'none' && outlineWidth > 0) ||
        style.boxShadow !== 'none' ||
        Number.parseFloat(svgFrameStyle?.strokeWidth ?? '0') >= 4)

    return {
      hasVisibleFocus,
      index,
      label: labelFor(activeElement),
    }
  }, focusableSelector)
}

async function expectKeyboardTraversal(page: Page, view: DeliveredView) {
  const expected = await focusableSnapshots(page)
  expect(expected.length, `${view.name} should expose focusable controls`).toBeGreaterThan(0)

  await blurActiveElement(page)
  const seen = new Set<number>()
  for (let step = 0; step < expected.length + 3; step += 1) {
    await page.keyboard.press('Tab')
    const active = await activeFocusableState(page)
    if (!active) {
      continue
    }
    expect(
      active.hasVisibleFocus,
      `${view.name}: focused element should have visible focus: ${active.label}`
    ).toBe(true)
    seen.add(active.index)
  }

  const missing = expected.filter((element) => !seen.has(element.index))
  expect(
    missing.map((element) => element.label),
    `${view.name} keyboard traversal should reach every visible focusable element`
  ).toEqual([])
}

async function tabTo(
  page: Page,
  locator: Locator,
  label: string
): Promise<void> {
  await blurActiveElement(page)
  for (let step = 0; step < 80; step += 1) {
    await page.keyboard.press('Tab')
    const hasFocus = await locator.evaluate(
      (element) => element === document.activeElement
    ).catch(() => false)
    if (hasFocus) {
      const active = await activeFocusableState(page)
      expect(active?.hasVisibleFocus, `${label} should show focus`).toBe(true)
      return
    }
  }
  throw new Error(`Could not reach ${label} by keyboard`)
}

async function expectNoHorizontalScrolling(page: Page): Promise<void> {
  const offenders = await page.evaluate(() => {
    function labelFor(element: Element): string {
      const text = element.textContent?.replace(/\s+/g, ' ').trim() ?? ''
      return [
        element.tagName.toLowerCase(),
        element.getAttribute('data-testid') ?? '',
        element.getAttribute('role') ?? '',
        element.getAttribute('aria-label') ?? '',
        text.slice(0, 80),
      ]
        .filter(Boolean)
        .join(' ')
    }

    return [
      document.documentElement,
      document.body,
      ...Array.from(document.querySelectorAll('*')),
    ]
      .filter((element) => {
        const style = window.getComputedStyle(element)
        const box = element.getBoundingClientRect()
        if (
          style.display === 'none' ||
          style.visibility === 'hidden' ||
          box.width === 0 ||
          box.height === 0
        ) {
          return false
        }
        const hasDocumentOverflow =
          element === document.documentElement &&
          element.scrollWidth > element.clientWidth + 1
        const hasScrollableOverflow =
          ['auto', 'scroll'].includes(style.overflowX) &&
          element.scrollWidth > element.clientWidth + 1
        return hasDocumentOverflow || hasScrollableOverflow
      })
      .map((element) => ({
        clientWidth: element.clientWidth,
        label: labelFor(element),
        overflowX: window.getComputedStyle(element).overflowX,
        scrollWidth: element.scrollWidth,
      }))
  })

  expect(offenders).toEqual([])
}

test('automated accessibility check covers every delivered view in both color modes @accessibility', async ({
  page,
}) => {
  for (const mode of colorModes) {
    for (const view of deliveredViews) {
      await openView(page, view, mode)
      const violations = await axeViolations(page)
      expect(
        violations,
        `${view.name} ${mode} should satisfy axe-core WCAG 2.2 AA tags:\n${axeSummary(
          violations
        )}`
      ).toEqual([])
    }
  }
})

test('automated accessibility check detects a seeded violation @accessibility', async ({
  page,
}) => {
  const [overview] = deliveredViews
  await openView(page, overview)
  await page.evaluate(() => {
    const image = document.createElement('img')
    image.src =
      'data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA='
    document.querySelector('main')?.prepend(image)
  })

  const violations = await axeViolations(page)
  expect(violations.map((violation) => violation.id)).toContain('image-alt')
})

test('keyboard traversal reaches every visible interactive element with visible focus @accessibility', async ({
  page,
}) => {
  for (const view of deliveredViews) {
    await openView(page, view)
    await expectKeyboardTraversal(page, view)
  }
})

test('keyboard operates route controls and tab frames without pointer input @accessibility', async ({
  page,
}) => {
  const overview = deliveredViews.find((view) => view.id === 'overview')
  const lessons = deliveredViews.find((view) => view.id === 'lessons')
  const tickets = deliveredViews.find((view) => view.id === 'tickets')
  const ticketDetail = deliveredViews.find((view) => view.id === 'ticket-detail')
  const criticalPath = deliveredViews.find((view) => view.id === 'critical-path')
  const dependencyGraph = deliveredViews.find(
    (view) => view.id === 'dependency-graph'
  )
  if (
    !overview ||
    !lessons ||
    !tickets ||
    !ticketDetail ||
    !criticalPath ||
    !dependencyGraph
  ) {
    throw new Error('Delivered view configuration is incomplete')
  }

  await openView(page, overview)
  await tabTo(page, page.getByRole('button', { name: /Search routes/ }), 'search routes')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog', { name: 'Command Palette' })).toBeVisible()
  await page.keyboard.type('Lessons')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/lessons$/)
  await expect(
    page.getByRole('heading', { exact: true, name: lessons.heading })
  ).toBeVisible()

  await tabTo(page, page.getByRole('tab', { name: /Draft/ }), 'draft lesson tab')
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: /Active/ })).toHaveAttribute(
    'aria-selected',
    'true'
  )
  await page.keyboard.press('ArrowLeft')
  await expect(page.getByRole('tab', { name: /Draft/ })).toHaveAttribute(
    'aria-selected',
    'true'
  )
  const lessonDetailButton = page.getByRole('button', {
    name: /View lesson details:/,
  }).first()
  const lessonDialogName = (
    await lessonDetailButton.getAttribute('aria-label')
  )?.replace('View lesson details: ', '')
  if (!lessonDialogName) {
    throw new Error('Seeded lesson detail button should include a lesson title')
  }
  await tabTo(page, lessonDetailButton, 'lesson detail button')
  await page.keyboard.press('Enter')
  await expect(
    page.getByRole('dialog', { name: lessonDialogName })
  ).toBeVisible()
  const promoteButton = page.getByRole('button', { name: 'Promote' })
  await tabTo(page, promoteButton, 'promote lesson')
  await page.keyboard.press('Enter')
  const loginDialog = page.getByRole('dialog', { name: 'Operator sign in' })
  await expect(loginDialog).toBeVisible()
  await expect(loginDialog.getByLabel('Bootstrap token')).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(loginDialog).toBeHidden()
  await expect(promoteButton).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(
    page.getByRole('dialog', { name: lessonDialogName })
  ).toBeHidden()

  await openView(page, tickets)
  await tabTo(page, page.getByRole('textbox', { name: 'Search tickets' }), 'ticket search')
  await page.keyboard.type('ATLAS-2')
  await expect(page.getByTestId('ticket-board-row')).toHaveCount(1)
  await tabTo(page, page.getByRole('button', { name: 'Group by epic' }), 'group by epic')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/mode=epic/)
  await tabTo(
    page,
    page.getByRole('button', { name: /Show terminal/ }),
    'show terminal tickets'
  )
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('ticket-board-row')).toHaveCount(1)

  await openView(page, ticketDetail)
  await tabTo(
    page,
    page.getByRole('tab', { name: 'Definition' }),
    'ticket definition tab'
  )
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: 'Metadata' })).toHaveAttribute(
    'aria-selected',
    'true'
  )
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: 'Evidence' })).toHaveAttribute(
    'aria-selected',
    'true'
  )
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: 'Dependencies' })).toHaveAttribute(
    'aria-selected',
    'true'
  )

  await openView(page, dependencyGraph)
  await tabTo(
    page,
    page.getByRole('button', { name: 'Show terminal statuses' }),
    'show terminal statuses'
  )
  await page.keyboard.press('Enter')
  await expect(page.getByText('Terminal shown')).toBeVisible()
  await tabTo(
    page,
    page.getByTestId(`dependency-node-link-${OPERATOR_TICKET_DETAIL_PLACEHOLDER_KEY}`),
    'dependency graph ticket link'
  )
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(
    new RegExp(`/tickets/${OPERATOR_TICKET_DETAIL_PLACEHOLDER_KEY}$`)
  )

  await openView(page, criticalPath)
  await tabTo(
    page,
    page.getByTestId('critical-path-step-link'),
    'critical path ticket link'
  )
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(new RegExp(`/tickets/${seededCriticalPathHead}$`))
})

test('session and destructive confirmation dialogs trap and return focus without axe violations @accessibility', async ({
  page,
}) => {
  await page.goto('/lessons')
  await page.getByRole('button', { name: 'Sign in' }).click()
  const loginDialog = page.getByRole('dialog', { name: 'Operator sign in' })
  await expect(loginDialog.getByLabel('Bootstrap token')).toBeFocused()
  expect(await axeViolations(page)).toEqual([])
  await loginDialog.getByLabel('Bootstrap token').fill(E2E_OPERATOR_TOKEN)
  await loginDialog.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()

  await page.getByRole('button', { name: /View lesson details:/ }).first().click()
  const rejectButton = page.getByRole('button', { name: 'Reject' })
  await rejectButton.click()
  const confirmation = page.getByRole('alertdialog', {
    name: 'Confirm lesson rejection',
  })
  await expect(confirmation).toBeVisible()
  await expect(confirmation).toContainText('archives it for audit')
  expect(await axeViolations(page)).toEqual([])

  for (let step = 0; step < 5; step += 1) {
    await page.keyboard.press('Tab')
    const focusInside = await page.evaluate(() =>
      Boolean(document.activeElement?.closest('[role="alertdialog"]'))
    )
    expect(focusInside).toBe(true)
  }
  await page.keyboard.press('Escape')
  await expect(confirmation).toBeHidden()
  await expect(rejectButton).toBeFocused()
})

test('data tables and tab frames expose roles and accessible labels @accessibility', async ({
  page,
}) => {
  const tickets = deliveredViews.find((view) => view.id === 'tickets')
  const ticketDetail = deliveredViews.find((view) => view.id === 'ticket-detail')
  const criticalPath = deliveredViews.find((view) => view.id === 'critical-path')
  const lessons = deliveredViews.find((view) => view.id === 'lessons')
  if (!tickets || !ticketDetail || !criticalPath || !lessons) {
    throw new Error('Delivered view configuration is incomplete')
  }

  await openView(page, tickets)
  await expect(
    page.getByRole('table', { name: 'Ticket board results' })
  ).toBeVisible()
  for (const header of ['Key', 'Title', 'Status', 'Type', 'Priority', 'Risk']) {
    await expect(page.getByRole('columnheader', { name: header })).toBeVisible()
  }

  await page.goto('/tickets?mode=epic')
  await expect(
    page.getByRole('heading', { exact: true, name: tickets.heading })
  ).toBeVisible()
  await expect(
    page.getByRole('table', { name: 'Tickets in ATLAS-E1' })
  ).toBeVisible()

  await openView(page, ticketDetail)
  await expect(
    page.getByRole('tablist', { name: 'Ticket detail sections' })
  ).toBeVisible()
  for (const tab of ['Definition', 'Metadata', 'Evidence', 'Dependencies']) {
    await expect(page.getByRole('tab', { name: tab })).toBeVisible()
  }
  await expect(page.getByRole('tabpanel', { name: 'Definition' })).toBeVisible()

  await openView(page, criticalPath)
  await expect(
    page.getByRole('table', { name: 'Critical path execution chain' })
  ).toBeVisible()
  for (const header of ['Step', 'Ticket', 'Effort', 'Cumulative']) {
    await expect(page.getByRole('columnheader', { name: header })).toBeVisible()
  }

  await openView(page, lessons)
  await expect(
    page.getByRole('tablist', { name: 'Lesson status facets' })
  ).toBeVisible()
  await expect(page.getByRole('table', { name: 'Lessons' })).toBeVisible()
})

for (const viewport of responsiveViewports) {
  test(`every delivered view avoids horizontal scrolling at ${viewport.name} width @accessibility`, async ({
    page,
  }) => {
    await page.setViewportSize({
      height: viewport.height,
      width: viewport.width,
    })

    for (const view of deliveredViews) {
      await openView(page, view)
      await expectNoHorizontalScrolling(page)
    }
  })
}
