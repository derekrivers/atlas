import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

describe('vendored shadcn-admin attribution', () => {
  it('keeps the upstream MIT license and versioned attribution', () => {
    const notice = readFileSync(
      join(appRoot, 'THIRD_PARTY_NOTICES.md'),
      'utf8'
    )

    expect(notice).toContain('satnaing/shadcn-admin')
    expect(notice).toContain('v2.2.1')
    expect(notice).toContain('MIT License')
    expect(notice).toContain('Copyright (c) 2024 Sat Naing')
    expect(notice).toContain('THE SOFTWARE IS PROVIDED "AS IS"')
  })
})
