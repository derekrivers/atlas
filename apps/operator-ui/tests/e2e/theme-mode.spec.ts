import { expect, test, type Locator, type Page } from '@playwright/test'

type Rgba = {
  r: number
  g: number
  b: number
  a: number
}

const apiBaseURL =
  process.env.ATLAS_OPERATOR_E2E_API_URL ?? 'http://127.0.0.1:18000'

function alphaFromToken(token: string | undefined): number {
  if (!token) return 1
  if (token.endsWith('%')) return Number(token.slice(0, -1)) / 100
  return Number(token)
}

function rgbChannelFromToken(token: string): number {
  if (token.endsWith('%')) return (Number(token.slice(0, -1)) / 100) * 255
  return Number(token)
}

function parseFunctionalParts(value: string): {
  channels: string[]
  alpha: number
} {
  const [channelPart, alphaPart] = value.split('/').map((part) => part.trim())
  return {
    channels: channelPart.split(/[\s,]+/).filter(Boolean),
    alpha: alphaFromToken(alphaPart),
  }
}

function parseRgb(value: string): Rgba | undefined {
  const match = value.match(/^rgba?\((.*)\)$/)
  if (!match) return undefined

  const { channels, alpha } = parseFunctionalParts(match[1])
  const [r, g, b] = channels.map(rgbChannelFromToken)
  if (r === undefined || g === undefined || b === undefined) return undefined

  return { r, g, b, a: alpha }
}

function parseSrgb(value: string): Rgba | undefined {
  const match = value.match(/^color\(srgb\s+(.*)\)$/)
  if (!match) return undefined

  const { channels, alpha } = parseFunctionalParts(match[1])
  const [r, g, b] = channels.map((token) => Number(token) * 255)
  if (r === undefined || g === undefined || b === undefined) return undefined

  return { r, g, b, a: alpha }
}

function lightnessFromToken(token: string): number {
  return token.endsWith('%') ? Number(token.slice(0, -1)) / 100 : Number(token)
}

function oklabToRgb(lightness: number, a: number, b: number): Rgba {
  const lPrime = lightness + 0.3963377774 * a + 0.2158037573 * b
  const mPrime = lightness - 0.1055613458 * a - 0.0638541728 * b
  const sPrime = lightness - 0.0894841775 * a - 1.291485548 * b

  const l = lPrime ** 3
  const m = mPrime ** 3
  const s = sPrime ** 3

  const linearR = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
  const linearG = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
  const linearB = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s

  const toSrgb = (channel: number) => {
    const encoded =
      channel <= 0.0031308
        ? 12.92 * channel
        : 1.055 * channel ** (1 / 2.4) - 0.055
    return Math.min(255, Math.max(0, encoded * 255))
  }

  return {
    r: toSrgb(linearR),
    g: toSrgb(linearG),
    b: toSrgb(linearB),
    a: 1,
  }
}

function parseOklch(value: string): Rgba | undefined {
  const match = value.match(/^oklch\((.*)\)$/)
  if (!match) return undefined

  const { channels, alpha } = parseFunctionalParts(match[1])
  const [lightnessToken, chromaToken, hueToken] = channels
  if (!lightnessToken || !chromaToken || !hueToken) return undefined

  const lightness = lightnessFromToken(lightnessToken)
  const chroma = Number(chromaToken)
  const hue = hueToken === 'none' ? 0 : Number.parseFloat(hueToken)
  const radians = (hue * Math.PI) / 180
  const rgb = oklabToRgb(
    lightness,
    chroma * Math.cos(radians),
    chroma * Math.sin(radians)
  )

  return { ...rgb, a: alpha }
}

function parseOklab(value: string): Rgba | undefined {
  const match = value.match(/^oklab\((.*)\)$/)
  if (!match) return undefined

  const { channels, alpha } = parseFunctionalParts(match[1])
  const [lightnessToken, aToken, bToken] = channels
  if (!lightnessToken || !aToken || !bToken) return undefined

  const rgb = oklabToRgb(
    lightnessFromToken(lightnessToken),
    Number(aToken),
    Number(bToken)
  )

  return { ...rgb, a: alpha }
}

function parseCssColor(value: string): Rgba {
  const parsed =
    parseRgb(value) ??
    parseSrgb(value) ??
    parseOklch(value) ??
    parseOklab(value) ??
    undefined

  if (!parsed) {
    throw new Error(`Unsupported CSS colour: ${value}`)
  }
  return parsed
}

function relativeLuminance({ r, g, b }: Rgba): number {
  const transform = (channel: number) => {
    const srgb = channel / 255
    return srgb <= 0.03928
      ? srgb / 12.92
      : ((srgb + 0.055) / 1.055) ** 2.4
  }

  return 0.2126 * transform(r) + 0.7152 * transform(g) + 0.0722 * transform(b)
}

function contrastRatio(foreground: Rgba, background: Rgba): number {
  const foregroundLuminance = relativeLuminance(foreground)
  const backgroundLuminance = relativeLuminance(background)
  const lighter = Math.max(foregroundLuminance, backgroundLuminance)
  const darker = Math.min(foregroundLuminance, backgroundLuminance)
  return (lighter + 0.05) / (darker + 0.05)
}

async function chooseTheme(page: Page, label: 'Light' | 'Dark') {
  const menu = page.locator('[data-slot="dropdown-menu-content"]')

  await expect(menu).toBeHidden()
  await page.getByRole('button', { name: 'Toggle theme' }).click()
  await expect(menu).toBeVisible()
  await menu.getByRole('menuitem', { name: label }).click()
  await expect(menu).toBeHidden()
  await expect(page.locator('html')).toHaveClass(
    new RegExp(`\\b${label.toLowerCase()}\\b`)
  )
}

async function effectiveStyles(locator: Locator) {
  return locator.evaluate((element) => {
    let current: Element | null = element
    while (current) {
      const backgroundColor = getComputedStyle(current).backgroundColor
      if (
        backgroundColor !== 'rgba(0, 0, 0, 0)' &&
        backgroundColor !== 'transparent'
      ) {
        return {
          color: getComputedStyle(element).color,
          backgroundColor,
        }
      }
      current = current.parentElement
    }

    return {
      color: getComputedStyle(element).color,
      backgroundColor: getComputedStyle(document.body).backgroundColor,
    }
  })
}

async function expectLegible(
  name: string,
  locator: Locator,
  minimumRatio = 4.5
) {
  const styles = await effectiveStyles(locator)
  const ratio = contrastRatio(
    parseCssColor(styles.color),
    parseCssColor(styles.backgroundColor)
  )

  expect(
    ratio,
    `${name}: ${styles.color} on ${styles.backgroundColor}`
  ).toBeGreaterThanOrEqual(minimumRatio)
}

async function expectVisiblePrimitivesLegible(page: Page, mode: 'light' | 'dark') {
  await expectLegible(`${mode} body`, page.locator('body'))
  await expectLegible(
    `${mode} theme toggle`,
    page.getByRole('button', { name: 'Toggle theme' })
  )
  await expectLegible(
    `${mode} placeholder badge`,
    page.locator('[data-slot="badge"]').filter({ hasText: 'Placeholder' })
  )
  await expectLegible(`${mode} card surface`, page.locator('section > div').first())
}

test('theme mode selection persists and light/dark primitives remain legible', async ({
  page,
  request,
}) => {
  const status = await request.get(`${apiBaseURL}/api/v1/status`)
  expect(status.ok()).toBe(true)
  await expect(status.json()).resolves.toMatchObject({ ticket_count: 5 })

  await page.goto('/')

  await chooseTheme(page, 'Light')
  await expectVisiblePrimitivesLegible(page, 'light')

  await chooseTheme(page, 'Dark')
  await expectVisiblePrimitivesLegible(page, 'dark')

  await page.reload()
  await expect(page.locator('html')).toHaveClass(/\bdark\b/)
  await expectVisiblePrimitivesLegible(page, 'dark')
})

test.describe('first visit colour-scheme preference', () => {
  test('uses the operating-system dark preference with no stored mode', async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme: 'dark' })
    await page.goto('/')

    await expect(page.locator('html')).toHaveClass(/\bdark\b/)
  })

  test('uses the operating-system light preference with no stored mode', async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme: 'light' })
    await page.goto('/')

    await expect(page.locator('html')).toHaveClass(/\blight\b/)
  })
})
