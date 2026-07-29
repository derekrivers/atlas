import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import type { AtlasRouteResponse } from '@/api/client'
import { buildDependencyGraphLayout } from '@/features/dependency-graph/dependency-graph-layout'
import { DependencyGraphView } from '@/features/dependency-graph/dependency-graph-view'

type DependencyGraphResponse =
  AtlasRouteResponse<'/api/v1/dependencies/graph'>
type DependencyCriticalPathResponse =
  AtlasRouteResponse<'/api/v1/dependencies/critical-path'>

let mountedRoot: Root | undefined
let container: HTMLDivElement | undefined

const graph = {
  nodes: [
    { key: 'ADR-0008', node_type: 'adr', status: 'accepted' },
    { key: 'ATLAS-1', node_type: 'ticket', status: 'done' },
    { key: 'ATLAS-2', node_type: 'ticket', status: 'in_progress' },
    { key: 'ATLAS-3', node_type: 'ticket', status: 'planned' },
  ],
  edges: [
    { source: 'ATLAS-2', target: 'ADR-0008', dependency_type: 'depends_on' },
    { source: 'ATLAS-2', target: 'ATLAS-1', dependency_type: 'depends_on' },
    { source: 'ATLAS-3', target: 'ATLAS-2', dependency_type: 'depends_on' },
  ],
} satisfies DependencyGraphResponse

const criticalPath = {
  keys: ['ATLAS-2', 'ATLAS-3'],
  steps: [
    { key: 'ATLAS-2', effort: 3, cumulative_effort: 3 },
    { key: 'ATLAS-3', effort: 2, cumulative_effort: 5 },
  ],
  total_effort: 5,
} satisfies DependencyCriticalPathResponse

async function render(component: ReactNode) {
  container = document.createElement('div')
  document.body.append(container)
  mountedRoot = createRoot(container)

  await act(async () => {
    mountedRoot?.render(component)
  })
}

function terminalToggle(): HTMLButtonElement {
  const button = Array.from(document.querySelectorAll('button')).find((element) =>
    element.textContent?.includes('terminal statuses')
  )
  if (!(button instanceof HTMLButtonElement)) {
    throw new Error('Terminal status toggle not found')
  }
  return button
}

afterEach(() => {
  mountedRoot?.unmount()
  container?.remove()
  mountedRoot = undefined
  container = undefined
})

describe('dependency graph browser rendering', () => {
  it('computes layout coordinates client-side without sampling nodes', () => {
    const layout = buildDependencyGraphLayout(graph, {
      criticalPathKeys: criticalPath.keys,
      showTerminalStatuses: true,
    })

    expect(layout.nodes).toHaveLength(graph.nodes.length)
    expect(layout.edges).toHaveLength(graph.edges.length)
    expect(layout.nodes.every((node) => Number.isFinite(node.x))).toBe(true)
    expect(layout.nodes.every((node) => Number.isFinite(node.y))).toBe(true)
  })

  it('hides terminal ticket statuses until the operator reveals them', async () => {
    await render(<DependencyGraphView criticalPath={criticalPath} graph={graph} />)

    expect(document.body.textContent).toContain('No render cap')
    expect(document.body.textContent).toContain('3 of 4')
    expect(document.body.textContent).toContain('2 of 3')
    expect(document.body.textContent).toContain('1 terminal hidden')
    expect(document.querySelector('[data-node-key="ATLAS-1"]')).toBeNull()

    await act(async () => {
      terminalToggle().click()
    })

    expect(document.body.textContent).toContain('4 of 4')
    expect(document.body.textContent).toContain('3 of 3')
    expect(document.body.textContent).toContain('Terminal shown')
    expect(document.querySelector('[data-node-key="ATLAS-1"]')).not.toBeNull()
  })

  it('renders critical path nodes and edges with distinguishable styling', async () => {
    await render(<DependencyGraphView criticalPath={criticalPath} graph={graph} />)

    const criticalNode = document.querySelector('[data-node-key="ATLAS-3"]')
    const criticalEdge = document.querySelector(
      '[data-edge-source="ATLAS-3"][data-edge-target="ATLAS-2"]'
    )

    expect(criticalNode?.getAttribute('data-critical')).toBe('true')
    expect(
      criticalNode
        ?.querySelector('[data-testid="dependency-graph-node-frame"]')
        ?.getAttribute('class')
    ).toContain('stroke-primary')
    expect(criticalEdge?.getAttribute('data-critical')).toBe('true')
    expect(criticalEdge?.getAttribute('class')).toContain('stroke-primary')
  })

  it('links ticket nodes to detail without edge mutation controls', async () => {
    await render(<DependencyGraphView criticalPath={criticalPath} graph={graph} />)

    expect(
      document
        .querySelector('[data-testid="dependency-node-link-ATLAS-2"]')
        ?.getAttribute('href')
    ).toBe('/tickets/ATLAS-2')
    expect(document.body.textContent).not.toMatch(
      /\b(add edge|create edge|delete edge|remove edge|reparent)\b/i
    )
  })
})
