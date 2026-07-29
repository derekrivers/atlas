import type { AtlasRouteResponse } from '@/api/client'
import { isTerminalTicketStatus } from '@/features/tickets/ticket-board-state'

type DependencyGraphResponse =
  AtlasRouteResponse<'/api/v1/dependencies/graph'>
type DependencyGraphNode = DependencyGraphResponse['nodes'][number]
type DependencyGraphEdge = DependencyGraphResponse['edges'][number]

export const NODE_WIDTH = 152
export const NODE_HEIGHT = 64

const COLUMN_GAP = 112
const ROW_GAP = 28
const GRAPH_MARGIN = 36
const MIN_GRAPH_WIDTH = 760
const MIN_GRAPH_HEIGHT = 420

type LayoutOptions = {
  criticalPathKeys: readonly string[]
  showTerminalStatuses: boolean
}

export type LayoutNode = DependencyGraphNode & {
  isCritical: boolean
  isTerminal: boolean
  x: number
  y: number
}

export type LayoutEdge = DependencyGraphEdge & {
  d: string
  isCritical: boolean
}

export type DependencyGraphLayout = {
  edges: LayoutEdge[]
  hiddenTerminalCount: number
  nodes: LayoutNode[]
  totalEdgeCount: number
  totalNodeCount: number
  viewBoxHeight: number
  viewBoxWidth: number
}

export function isTerminalTicket(node: DependencyGraphNode): boolean {
  return node.node_type === 'ticket' && isTerminalTicketStatus(node.status)
}

function naturalCompare(left: string, right: string): number {
  return left.localeCompare(right, 'en', {
    numeric: true,
    sensitivity: 'base',
  })
}

function criticalEdgeKey(source: string, target: string): string {
  return `${source}\u0000${target}`
}

function criticalDependencyEdges(keys: readonly string[]): Set<string> {
  const edges = new Set<string>()
  for (let index = 1; index < keys.length; index += 1) {
    edges.add(criticalEdgeKey(keys[index], keys[index - 1]))
  }
  return edges
}

function rankGraph(
  nodes: readonly DependencyGraphNode[],
  edges: readonly DependencyGraphEdge[]
): Map<string, number> {
  const visibleKeys = new Set(nodes.map((node) => node.key))
  const targetsBySource = new Map<string, string[]>()

  for (const edge of edges) {
    if (!visibleKeys.has(edge.source) || !visibleKeys.has(edge.target)) {
      continue
    }
    const targets = targetsBySource.get(edge.source) ?? []
    targets.push(edge.target)
    targetsBySource.set(edge.source, targets)
  }

  const ranks = new Map<string, number>()
  const visiting = new Set<string>()

  function rankFor(key: string): number {
    const existing = ranks.get(key)
    if (existing !== undefined) {
      return existing
    }
    if (visiting.has(key)) {
      return 0
    }

    visiting.add(key)
    const dependencyRanks = (targetsBySource.get(key) ?? []).map(rankFor)
    visiting.delete(key)
    const rank =
      dependencyRanks.length === 0 ? 0 : Math.max(...dependencyRanks) + 1
    ranks.set(key, rank)
    return rank
  }

  for (const node of nodes) {
    rankFor(node.key)
  }

  return ranks
}

function edgePath(source: LayoutNode, target: LayoutNode): string {
  if (source.x > target.x) {
    const startX = source.x
    const startY = source.y + NODE_HEIGHT / 2
    const endX = target.x + NODE_WIDTH
    const endY = target.y + NODE_HEIGHT / 2
    const bend = Math.max(48, Math.abs(startX - endX) / 2)
    return [
      `M ${startX} ${startY}`,
      `C ${startX - bend} ${startY}`,
      `${endX + bend} ${endY}`,
      `${endX} ${endY}`,
    ].join(' ')
  }

  const startX = source.x + NODE_WIDTH
  const startY = source.y + NODE_HEIGHT / 2
  const endX = target.x
  const endY = target.y + NODE_HEIGHT / 2
  const bend = Math.max(48, Math.abs(endX - startX) / 2)
  return [
    `M ${startX} ${startY}`,
    `C ${startX + bend} ${startY}`,
    `${endX - bend} ${endY}`,
    `${endX} ${endY}`,
  ].join(' ')
}

export function buildDependencyGraphLayout(
  graph: DependencyGraphResponse,
  { criticalPathKeys, showTerminalStatuses }: LayoutOptions
): DependencyGraphLayout {
  const criticalNodes = new Set(criticalPathKeys)
  const criticalEdges = criticalDependencyEdges(criticalPathKeys)
  const hiddenTerminalCount = showTerminalStatuses
    ? 0
    : graph.nodes.filter(isTerminalTicket).length
  const visibleNodes = graph.nodes.filter(
    (node) => showTerminalStatuses || !isTerminalTicket(node)
  )
  const visibleKeys = new Set(visibleNodes.map((node) => node.key))
  const visibleEdges = graph.edges.filter(
    (edge) => visibleKeys.has(edge.source) && visibleKeys.has(edge.target)
  )
  const rankByKey = rankGraph(visibleNodes, visibleEdges)
  const criticalOrder = new Map(
    criticalPathKeys.map((key, index) => [key, index] as const)
  )
  const columns = new Map<number, DependencyGraphNode[]>()

  for (const node of visibleNodes) {
    const rank = rankByKey.get(node.key) ?? 0
    const column = columns.get(rank) ?? []
    column.push(node)
    columns.set(rank, column)
  }

  for (const column of columns.values()) {
    column.sort((left, right) => {
      const leftCritical = criticalOrder.get(left.key) ?? Number.MAX_SAFE_INTEGER
      const rightCritical =
        criticalOrder.get(right.key) ?? Number.MAX_SAFE_INTEGER
      if (leftCritical !== rightCritical) {
        return leftCritical - rightCritical
      }
      return naturalCompare(left.key, right.key)
    })
  }

  const orderedRanks = [...columns.keys()].sort((left, right) => left - right)
  const largestColumnSize = Math.max(
    1,
    ...orderedRanks.map((rank) => columns.get(rank)?.length ?? 0)
  )
  const tallestColumn =
    largestColumnSize * NODE_HEIGHT + (largestColumnSize - 1) * ROW_GAP
  const viewBoxWidth = Math.max(
    MIN_GRAPH_WIDTH,
    GRAPH_MARGIN * 2 +
      orderedRanks.length * NODE_WIDTH +
      Math.max(0, orderedRanks.length - 1) * COLUMN_GAP
  )
  const viewBoxHeight = Math.max(
    MIN_GRAPH_HEIGHT,
    GRAPH_MARGIN * 2 + tallestColumn
  )
  const layoutNodes = new Map<string, LayoutNode>()

  orderedRanks.forEach((rank, columnIndex) => {
    const column = columns.get(rank) ?? []
    const columnHeight =
      column.length * NODE_HEIGHT + (column.length - 1) * ROW_GAP
    const x = GRAPH_MARGIN + columnIndex * (NODE_WIDTH + COLUMN_GAP)
    let y = GRAPH_MARGIN + Math.max(0, tallestColumn - columnHeight) / 2

    for (const node of column) {
      layoutNodes.set(node.key, {
        ...node,
        isCritical: criticalNodes.has(node.key),
        isTerminal: isTerminalTicket(node),
        x,
        y,
      })
      y += NODE_HEIGHT + ROW_GAP
    }
  })

  const nodes = [...layoutNodes.values()]
  const edges = visibleEdges
    .map((edge) => {
      const source = layoutNodes.get(edge.source)
      const target = layoutNodes.get(edge.target)
      if (!source || !target) {
        return undefined
      }
      return {
        ...edge,
        d: edgePath(source, target),
        isCritical: criticalEdges.has(criticalEdgeKey(edge.source, edge.target)),
      }
    })
    .filter((edge): edge is LayoutEdge => edge !== undefined)

  return {
    edges,
    hiddenTerminalCount,
    nodes,
    totalEdgeCount: graph.edges.length,
    totalNodeCount: graph.nodes.length,
    viewBoxHeight,
    viewBoxWidth,
  }
}
