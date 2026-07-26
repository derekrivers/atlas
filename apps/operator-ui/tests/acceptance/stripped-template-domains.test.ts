import { execFileSync } from 'node:child_process'
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const sourceRoot = join(appRoot, 'src')

function grepSource(pattern: string): string {
  try {
    return execFileSync('grep', ['-RInE', pattern, sourceRoot], {
      encoding: 'utf8',
    })
  } catch (error) {
    if (
      typeof error === 'object' &&
      error !== null &&
      'status' in error &&
      error.status === 1
    ) {
      return ''
    }
    throw error
  }
}

function packageMetadata(): string {
  return [
    readFileSync(join(appRoot, 'package.json'), 'utf8'),
    readFileSync(join(appRoot, 'package-lock.json'), 'utf8'),
  ].join('\n')
}

function sourcePaths(root: string): string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name)
    if (entry.isDirectory()) return [path, ...sourcePaths(path)]
    if (entry.isFile()) return [path]
    return []
  })
}

describe('stripped shadcn-admin demo domains', () => {
  it('does not resolve deleted authentication or fixture packages', () => {
    const metadata = packageMetadata()
    const clerk = ['@', 'clerk', '/', 'clerk-react'].join('')
    const faker = ['@', 'faker-js', '/', 'faker'].join('')

    expect(metadata).not.toContain(clerk)
    expect(metadata).not.toContain(faker)
  })

  it('has no demo route literals left in source', () => {
    for (const route of [
      'users',
      'chats',
      'tasks',
      'apps',
      'help-center',
      'settings',
    ]) {
      const pattern = `["']/${route}(/|["']|$)`
      expect(grepSource(pattern), `${route} route literal`).toBe('')
    }
  })

  it('has no demo domain paths left in source', () => {
    const paths = sourcePaths(sourceRoot).map((path) =>
      path.slice(sourceRoot.length + 1).toLowerCase()
    )

    for (const route of [
      'users',
      'chats',
      'tasks',
      'apps',
      'help-center',
      'settings',
    ]) {
      const pathHits = paths.filter((path) =>
        path.split('/').some((segment) => segment === route)
      )
      expect(pathHits, `${route} source path`).toEqual([])
    }
  })
})
