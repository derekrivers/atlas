import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const repoRoot = join(appRoot, '..', '..')

describe('vendored shadcn-admin attribution', () => {
  it('keeps the upstream MIT license and versioned attribution for source and theme', () => {
    const notice = readFileSync(
      join(appRoot, 'THIRD_PARTY_NOTICES.md'),
      'utf8'
    )
    const repositoryLicense = readFileSync(join(repoRoot, 'LICENSE'), 'utf8')

    expect(notice).toContain('satnaing/shadcn-admin')
    expect(notice).toContain('v2.2.1')
    expect(notice).toContain('vendored source')
    expect(notice).toContain('src/styles/theme.css')
    expect(notice).toContain('vendored theme')
    expect(notice).toContain('license obligation')
    expect(notice).toContain('MIT License')
    expect(notice).toContain('Copyright (c) 2024 Sat Naing')
    expect(notice).toContain('THE SOFTWARE IS PROVIDED "AS IS"')
    expect(repositoryLicense).toContain('MIT License')
    expect(repositoryLicense).toContain('Copyright (c) 2026 Derek Rivers')
  })
})
