import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const sourceRoot = join(appRoot, 'src')
const tokenFile = join(sourceRoot, 'styles', 'theme.css')
const topLevelSourceFiles = [join(appRoot, 'index.html')]

type SourceViolation = {
  path: string
  line: number
  match: string
}

type ContractRule = {
  name: string
  pattern: RegExp
}

const rules: ContractRule[] = [
  {
    name: 'colour function or hex literal',
    pattern: /#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|oklch)\(/g,
  },
  {
    name: 'raw radius value',
    pattern:
      /\brounded-\[(?:[0-9]*\.)?[0-9]+(?:px|rem|em)\]|\b(?:borderRadius|border-radius)\s*[:=]\s*['"]?(?:[0-9]*\.)?[0-9]+(?:px|rem|em)/g,
  },
  {
    name: 'font-family value outside token file',
    pattern:
      /\bfontFamily\b|\bfont-family\s*:|['"](?:Inter|Manrope|Arial|Helvetica|sans-serif|serif|monospace)['"]/g,
  },
]

function sourcePaths(root: string): string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name)
    if (entry.isDirectory()) return sourcePaths(path)
    if (entry.isFile()) return [path]
    return []
  })
}

function violationsFor(rule: ContractRule): SourceViolation[] {
  const paths = [...sourcePaths(sourceRoot), ...topLevelSourceFiles].filter(
    (path) => path !== tokenFile
  )

  return paths.flatMap((path) => {
    const text = readFileSync(path, 'utf8')
    const lines = text.split('\n')

    return lines.flatMap((lineText, index) =>
      Array.from(lineText.matchAll(rule.pattern), (match) => ({
        path: relative(appRoot, path).split(sep).join('/'),
        line: index + 1,
        match: match[0],
      }))
    )
  })
}

function formatViolations(violations: SourceViolation[]): string {
  return violations
    .map(({ path, line, match }) => `${path}:${line}: ${match}`)
    .join('\n')
}

describe('vendored theme token source contract', () => {
  it('keeps hardcoded colours, radii, and font values out of source files', () => {
    for (const rule of rules) {
      const violations = violationsFor(rule)

      expect(formatViolations(violations), rule.name).toBe('')
    }
  })
})
