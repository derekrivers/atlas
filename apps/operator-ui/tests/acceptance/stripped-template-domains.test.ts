import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const sourceRoot = join(appRoot, 'src')

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

  it('does not depend on remote font delivery', () => {
    const html = readFileSync(join(appRoot, 'index.html'), 'utf8')

    expect(html).not.toContain('fonts.googleapis.com')
    expect(html).not.toContain('fonts.gstatic.com')
  })

  it('has no demo URL paths in route or navigation surfaces', () => {
    const routeSurfaces = sourcePaths(sourceRoot).filter((path) => {
      const relative = path.slice(sourceRoot.length + 1).replaceAll('\\', '/')
      return (
        relative === 'router.tsx' ||
        relative.startsWith('routes/') ||
        relative === 'components/layout/app-sidebar.tsx' ||
        relative === 'components/layout/top-nav.tsx' ||
        relative === 'components/command-menu.tsx'
      )
    })
    const source = routeSurfaces
      .map((path) => `${path}\n${readFileSync(path, 'utf8')}`)
      .join('\n')

    for (const route of [
      'users',
      'chats',
      'tasks',
      'apps',
      'help-center',
      'settings',
    ]) {
      const pattern = new RegExp(`["'\`]/${route}(?:/|["'\`])`)
      expect(source, `${route} URL path`).not.toMatch(pattern)
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
