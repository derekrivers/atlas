import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

type LockPackage = {
  dependencies?: Record<string, string>
  devDependencies?: Record<string, string>
  version?: string
}

type PackageLock = {
  packages: Record<string, LockPackage>
}

type BrowserDescriptor = {
  browserVersion?: string
  installByDefault?: boolean
  name: string
  revision: string
}

type BrowserManifest = {
  browsers: BrowserDescriptor[]
}

const PLAYWRIGHT_VERSION = '1.62.0'
const CHROMIUM_BROWSER_VERSION = '151.0.7922.34'
const CHROMIUM_REVISION = '1234'

function readLockfile(): PackageLock {
  return JSON.parse(
    readFileSync(join(appRoot, 'package-lock.json'), 'utf8')
  ) as PackageLock
}

function readPlaywrightBrowserManifest(): BrowserManifest {
  return JSON.parse(
    readFileSync(
      join(appRoot, 'node_modules', 'playwright-core', 'browsers.json'),
      'utf8'
    )
  ) as BrowserManifest
}

function resolvedVersion(lockfile: PackageLock, packageName: string): string {
  const version = lockfile.packages[`node_modules/${packageName}`]?.version
  expect(
    version,
    `${packageName} must be resolved in package-lock.json`
  ).toEqual(
    expect.any(String)
  )
  return version as string
}

function expectMajor(
  lockfile: PackageLock,
  packageName: string,
  expectedMajor: number
) {
  const version = resolvedVersion(lockfile, packageName)
  expect(Number(version.split('.')[0]), `${packageName}@${version}`).toBe(
    expectedMajor
  )
}

function expectExact(
  lockfile: PackageLock,
  packageName: string,
  expectedVersion: string
) {
  const version = resolvedVersion(lockfile, packageName)
  expect(version, packageName).toBe(expectedVersion)
}

describe('resolved UI toolchain versions', () => {
  it('pins the scaffold stack in the committed lockfile', () => {
    const lockfile = readLockfile()

    expectMajor(lockfile, 'react', 19)
    expectMajor(lockfile, 'react-dom', 19)
    expectMajor(lockfile, 'typescript', 5)
    expectMajor(lockfile, 'vite', 7)
    expectMajor(lockfile, 'tailwindcss', 4)
    expectMajor(lockfile, '@tanstack/react-router', 1)
  })

  it('pins the Playwright browser through the committed lockfile', () => {
    const lockfile = readLockfile()
    const root = lockfile.packages['']

    expect(root.devDependencies?.['@playwright/test']).toBe(PLAYWRIGHT_VERSION)
    expect(root.devDependencies?.playwright).toBe(PLAYWRIGHT_VERSION)
    expectExact(lockfile, '@playwright/test', PLAYWRIGHT_VERSION)
    expectExact(lockfile, 'playwright', PLAYWRIGHT_VERSION)
    expectExact(lockfile, 'playwright-core', PLAYWRIGHT_VERSION)
    expect(
      lockfile.packages['node_modules/playwright']?.dependencies?.[
        'playwright-core'
      ]
    ).toBe(PLAYWRIGHT_VERSION)

    const manifest = readPlaywrightBrowserManifest()
    const chromium = manifest.browsers.find(
      (browser) => browser.name === 'chromium'
    )

    expect(chromium).toEqual({
      browserVersion: CHROMIUM_BROWSER_VERSION,
      installByDefault: true,
      name: 'chromium',
      revision: CHROMIUM_REVISION,
      title: 'Chrome for Testing',
    })
  })
})
