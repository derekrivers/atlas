import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

type LockPackage = {
  version?: string
}

type PackageLock = {
  packages: Record<string, LockPackage>
}

function readLockfile(): PackageLock {
  return JSON.parse(
    readFileSync(join(appRoot, 'package-lock.json'), 'utf8')
  ) as PackageLock
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
})
