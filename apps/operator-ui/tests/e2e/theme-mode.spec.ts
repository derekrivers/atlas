import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test, type Locator, type Page } from '@playwright/test'
import ts from 'typescript'

type Rgba = {
  r: number
  g: number
  b: number
  a: number
}

const apiBaseURL =
  process.env.ATLAS_OPERATOR_E2E_API_URL ?? 'http://127.0.0.1:18000'
const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const destructiveButtonClasses = readCvaVariantClasses(
  'src/components/ui/button.tsx',
  'buttonVariants',
  'destructive'
)
const destructiveBadgeClasses = readCvaVariantClasses(
  'src/components/ui/badge.tsx',
  'badgeVariants',
  'destructive'
)

function propertyNameText(name: ts.PropertyName): string | undefined {
  if (
    ts.isIdentifier(name) ||
    ts.isStringLiteral(name) ||
    ts.isNumericLiteral(name)
  ) {
    return name.text
  }
  return undefined
}

function stringValue(expression: ts.Expression | undefined): string | undefined {
  if (
    expression &&
    (ts.isStringLiteral(expression) ||
      ts.isNoSubstitutionTemplateLiteral(expression))
  ) {
    return expression.text
  }
  return undefined
}

function propertyAssignment(
  object: ts.ObjectLiteralExpression,
  name: string
): ts.PropertyAssignment | undefined {
  return object.properties.find(
    (property): property is ts.PropertyAssignment =>
      ts.isPropertyAssignment(property) &&
      propertyNameText(property.name) === name
  )
}

function objectInitializer(
  object: ts.ObjectLiteralExpression,
  name: string
): ts.ObjectLiteralExpression {
  const property = propertyAssignment(object, name)
  if (!property || !ts.isObjectLiteralExpression(property.initializer)) {
    throw new Error(`Missing object property: ${name}`)
  }
  return property.initializer
}

function stringInitializer(
  object: ts.ObjectLiteralExpression,
  name: string
): string {
  const value = stringValue(propertyAssignment(object, name)?.initializer)
  if (!value) throw new Error(`Missing string property: ${name}`)
  return value
}

function variableDeclaration(
  sourceFile: ts.SourceFile,
  name: string
): ts.VariableDeclaration {
  let found: ts.VariableDeclaration | undefined

  function visit(node: ts.Node) {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.name.text === name
    ) {
      found = node
      return
    }
    ts.forEachChild(node, visit)
  }

  visit(sourceFile)
  if (!found) throw new Error(`Missing variable declaration: ${name}`)
  return found
}

function readCvaVariantClasses(
  sourceRelativePath: string,
  declarationName: string,
  variantName: string
): string {
  const sourcePath = join(appRoot, sourceRelativePath)
  const sourceFile = ts.createSourceFile(
    sourcePath,
    readFileSync(sourcePath, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX
  )
  const declaration = variableDeclaration(sourceFile, declarationName)
  const initializer = declaration.initializer

  if (!initializer || !ts.isCallExpression(initializer)) {
    throw new Error(`${declarationName} is not a call expression`)
  }

  const [baseExpression, optionsExpression] = initializer.arguments
  const baseClasses = stringValue(baseExpression)

  if (!baseClasses || !optionsExpression) {
    throw new Error(`${declarationName} does not declare base classes`)
  }
  if (!ts.isObjectLiteralExpression(optionsExpression)) {
    throw new Error(`${declarationName} options are not an object`)
  }

  const variants = objectInitializer(optionsExpression, 'variants')
  const variant = objectInitializer(variants, 'variant')
  const selectedVariantClasses = stringInitializer(variant, variantName)

  return `${baseClasses} ${selectedVariantClasses}`
}

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

function composite(foreground: Rgba, background: Rgba): Rgba {
  const alpha = foreground.a + background.a * (1 - foreground.a)
  if (alpha === 0) return { r: 0, g: 0, b: 0, a: 0 }

  return {
    r:
      (foreground.r * foreground.a +
        background.r * background.a * (1 - foreground.a)) /
      alpha,
    g:
      (foreground.g * foreground.a +
        background.g * background.a * (1 - foreground.a)) /
      alpha,
    b:
      (foreground.b * foreground.a +
        background.b * background.a * (1 - foreground.a)) /
      alpha,
    a: alpha,
  }
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
    const bodyBackgroundColor = getComputedStyle(document.body).backgroundColor

    while (current) {
      const backgroundColor = getComputedStyle(current).backgroundColor
      if (
        backgroundColor !== 'rgba(0, 0, 0, 0)' &&
        backgroundColor !== 'transparent'
      ) {
        return {
          color: getComputedStyle(element).color,
          backgroundColor,
          bodyBackgroundColor,
        }
      }
      current = current.parentElement
    }

    return {
      color: getComputedStyle(element).color,
      backgroundColor: bodyBackgroundColor,
      bodyBackgroundColor,
    }
  })
}

async function expectLegible(
  name: string,
  locator: Locator,
  minimumRatio = 4.5
) {
  const styles = await effectiveStyles(locator)
  const background = parseCssColor(styles.backgroundColor)
  const bodyBackground = parseCssColor(styles.bodyBackgroundColor)
  const ratio = contrastRatio(
    parseCssColor(styles.color),
    background.a < 1 ? composite(background, bodyBackground) : background
  )

  expect(
    ratio,
    `${name}: ${styles.color} on ${styles.backgroundColor}`
  ).toBeGreaterThanOrEqual(minimumRatio)
}

async function mountDestructivePrimitiveProbe(page: Page) {
  await page.evaluate(
    ({ badgeClasses, buttonClasses }) => {
      document.querySelector('[data-e2e-primitive-probe]')?.remove()

      const probe = document.createElement('div')
      probe.dataset.e2ePrimitiveProbe = 'true'
      probe.style.position = 'fixed'
      probe.style.insetInlineStart = '1rem'
      probe.style.insetBlockEnd = '1rem'
      probe.style.display = 'flex'
      probe.style.gap = '0.5rem'
      probe.style.zIndex = '40'

      const button = document.createElement('button')
      button.dataset.slot = 'button'
      button.dataset.e2eDestructiveButton = 'true'
      button.className = buttonClasses
      button.textContent = 'Destructive button'

      const badge = document.createElement('span')
      badge.dataset.slot = 'badge'
      badge.dataset.e2eDestructiveBadge = 'true'
      badge.className = badgeClasses
      badge.textContent = 'Destructive badge'

      probe.append(button, badge)
      document.body.append(probe)
    },
    {
      badgeClasses: destructiveBadgeClasses,
      buttonClasses: destructiveButtonClasses,
    }
  )
}

async function expectCommandOverlayDimsPage(page: Page, mode: 'light' | 'dark') {
  await page.getByRole('button', { name: /Search routes/ }).click()

  const content = page.locator('[data-slot="dialog-content"]')
  const overlay = page.locator('[data-slot="dialog-overlay"]')
  await expect(content).toBeVisible()
  await expect(overlay).toBeVisible()

  const colors = await overlay.evaluate((element) => ({
    overlayBackgroundColor: getComputedStyle(element).backgroundColor,
    bodyBackgroundColor: getComputedStyle(document.body).backgroundColor,
  }))
  const pageBackground = parseCssColor(colors.bodyBackgroundColor)
  const dimmedBackground = composite(
    parseCssColor(colors.overlayBackgroundColor),
    pageBackground
  )

  expect(
    relativeLuminance(dimmedBackground),
    `${mode} overlay: ${colors.overlayBackgroundColor} over ${colors.bodyBackgroundColor}`
  ).toBeLessThan(relativeLuminance(pageBackground))

  await page.keyboard.press('Escape')
  await expect(content).toBeHidden()
}

async function expectVisiblePrimitivesLegible(page: Page, mode: 'light' | 'dark') {
  await mountDestructivePrimitiveProbe(page)
  await expectLegible(`${mode} body`, page.locator('body'))
  await expectLegible(
    `${mode} theme toggle`,
    page.getByRole('button', { name: 'Toggle theme' })
  )
  await expectLegible(
    `${mode} placeholder badge`,
    page.locator('[data-slot="badge"]').filter({ hasText: 'Placeholder' })
  )
  await expectLegible(
    `${mode} destructive button`,
    page.locator('[data-e2e-destructive-button="true"]')
  )
  await expectLegible(
    `${mode} destructive badge`,
    page.locator('[data-e2e-destructive-badge="true"]')
  )
  await expectLegible(`${mode} card surface`, page.locator('section > div').first())
  await expectCommandOverlayDimsPage(page, mode)
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
