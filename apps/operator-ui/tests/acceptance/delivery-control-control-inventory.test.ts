import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

const appRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

function read(path: string): string {
  return readFileSync(join(appRoot, path), 'utf8')
}

const featurePaths = [
  'src/features/delivery-control/delivery-control-view.tsx',
  'src/features/delivery-control/integration-pressure-console.tsx',
  'src/features/delivery-control/policy-editor.tsx',
] as const

function interactiveElements(source: string, path: string): string[] {
  const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const elements: string[] = []
  function visit(node: ts.Node): void {
    if (ts.isJsxElement(node)) {
      const name = node.openingElement.tagName.getText(file)
      if (name === 'Button' || name === 'button') {
        elements.push(node.getText(file))
      }
    }
    if (ts.isJsxSelfClosingElement(node)) {
      const name = node.tagName.getText(file)
      if (name === 'Button' || name === 'button') {
        elements.push(node.getText(file))
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return elements
}

describe('ATL-430 executable delivery-control inventory', () => {
  it('admits one policy mutation and no CI, tracker, GitHub, Git, Symphony, worker, or automatic concurrency command', () => {
    const sources = featurePaths.map((path) => ({ path, source: read(path) }))
    const completeSource = sources.map(({ source }) => source).join('\n')
    const mutationHooks = new Set(
      completeSource.match(/use[A-Z][A-Za-z]+Mutation/g) ?? []
    )
    expect([...mutationHooks]).toEqual(['useReplaceDeliveryAdmissionPolicyMutation'])

    const controls = sources.flatMap(({ path, source }) =>
      interactiveElements(source, path)
    )
    expect(controls.length).toBeGreaterThan(0)

    const prohibited = {
      'automatic concurrency/ramp control': /auto(?:matic)?[^<]{0,30}(?:concurrency|ramp)|increase concurrency/i,
      'CI retry/cancel': /(?:retry|cancel)[^<]{0,20}ci|ci[^<]{0,20}(?:retry|cancel)/i,
      'Git rebase/push': /(?:rebase|push)[^<]{0,20}(?:branch|git)|git[^<]{0,20}(?:rebase|push)/i,
      'GitHub update/merge': /(?:update|merge)[^<]{0,20}(?:github|pull request|branch)|github[^<]{0,20}(?:update|merge)/i,
      'Symphony worker control': /(?:start|stop|cancel|terminate|dispatch)[^<]{0,25}(?:symphony|worker)|(?:symphony|worker)[^<]{0,25}(?:start|stop|cancel|terminate|dispatch)/i,
      'ticket transition': /(?:promote|demote|transition|move)[^<]{0,20}ticket|ticket[^<]{0,20}(?:promote|demote|transition|move)/i,
    }
    for (const [category, pattern] of Object.entries(prohibited)) {
      expect(
        controls.filter((control) => pattern.test(control)),
        `${category} must have zero executable controls`
      ).toEqual([])
    }

    const client = read('src/api/client.ts')
    const policyCommand = client.slice(
      client.indexOf('export async function atlasReplaceDeliveryAdmissionPolicy'),
      client.indexOf('export async function atlasCreateAcceptanceSession')
    )
    expect(policyCommand).toContain("method: 'POST'")
    expect(policyCommand).toContain("requestPath: '/api/v1/delivery-control/policy'")
    expect(policyCommand.match(/requestPath:/g)).toHaveLength(1)
  })
})
