import { useId, useMemo, useState } from 'react'
import { Eye, EyeOff, GitBranch, Network } from 'lucide-react'
import {
  useDependencyCriticalPathQuery,
  useDependencyGraphQuery,
} from '@/api/query-hooks'
import type { AtlasRouteResponse } from '@/api/client'
import {
  NODE_HEIGHT,
  NODE_WIDTH,
  buildDependencyGraphLayout,
  type DependencyGraphLayout,
} from '@/features/dependency-graph/dependency-graph-layout'
import { ticketDetailHref } from '@/app-shell/surfaces'
import { Main } from '@/components/layout/main'
import {
  EmptyCollectionState,
  LoadingState,
  RequestErrorState,
} from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

type DependencyGraphResponse =
  AtlasRouteResponse<'/api/v1/dependencies/graph'>
type DependencyCriticalPathResponse =
  AtlasRouteResponse<'/api/v1/dependencies/critical-path'>

function statusLabel(status: string): string {
  return status.replace(/_/g, ' ')
}

function compactLabel(value: string): string {
  return value.length > 19 ? `${value.slice(0, 16)}...` : value
}

function GraphStat({
  label,
  value,
}: {
  label: string
  value: string
}) {
  return (
    <div className='border-border bg-card text-card-foreground rounded-lg border px-4 py-3'>
      <dt className='text-muted-foreground text-xs font-medium uppercase'>
        {label}
      </dt>
      <dd className='mt-1 text-lg font-semibold tracking-normal'>{value}</dd>
    </div>
  )
}

function DependencyGraphCanvas({
  layout,
}: {
  layout: DependencyGraphLayout
}) {
  const markerId = useId()
  const arrowId = `${markerId}-arrow`
  const criticalArrowId = `${markerId}-critical-arrow`

  return (
    <svg
      aria-label='Dependency graph'
      className='border-border bg-card h-[62svh] min-h-96 max-h-[760px] w-full rounded-lg border'
      data-testid='dependency-graph-svg'
      preserveAspectRatio='xMidYMid meet'
      role='group'
      viewBox={`0 0 ${layout.viewBoxWidth} ${layout.viewBoxHeight}`}
    >
      <defs>
        <marker
          id={arrowId}
          markerHeight='10'
          markerWidth='10'
          orient='auto'
          refX='9'
          refY='5'
          viewBox='0 0 10 10'
        >
          <path className='fill-muted-foreground' d='M 0 0 L 10 5 L 0 10 z' />
        </marker>
        <marker
          id={criticalArrowId}
          markerHeight='10'
          markerWidth='10'
          orient='auto'
          refX='9'
          refY='5'
          viewBox='0 0 10 10'
        >
          <path className='fill-primary' d='M 0 0 L 10 5 L 0 10 z' />
        </marker>
      </defs>
      <g fill='none'>
        {layout.edges.map((edge) => (
          <path
            className={cn(
              'transition-colors',
              edge.isCritical
                ? 'stroke-primary stroke-[3]'
                : 'stroke-muted-foreground/40 stroke-[1.5]'
            )}
            d={edge.d}
            data-critical={String(edge.isCritical)}
            data-edge-source={edge.source}
            data-edge-target={edge.target}
            data-testid='dependency-graph-edge'
            key={`${edge.source}->${edge.target}`}
            markerEnd={`url(#${edge.isCritical ? criticalArrowId : arrowId})`}
          />
        ))}
      </g>
      <g>
        {layout.nodes.map((node) => {
          const frame = (
            <g
              data-critical={String(node.isCritical)}
              data-node-key={node.key}
              data-node-status={node.status}
              data-node-type={node.node_type}
              data-testid='dependency-graph-node'
              key={node.key}
              transform={`translate(${node.x} ${node.y})`}
            >
              <title>
                {node.key} {node.node_type} {statusLabel(node.status)}
              </title>
              <rect
                className={cn(
                  'stroke-border fill-background',
                  node.isCritical && 'stroke-primary fill-primary/10 stroke-[3]',
                  node.isTerminal && 'fill-muted/50'
                )}
                data-testid='dependency-graph-node-frame'
                height={NODE_HEIGHT}
                rx='6'
                width={NODE_WIDTH}
              />
              {node.isCritical ? (
                <rect
                  aria-hidden='true'
                  className='fill-primary'
                  height={NODE_HEIGHT - 12}
                  rx='2'
                  width='4'
                  x='8'
                  y='6'
                />
              ) : null}
              <text
                className='fill-foreground text-[14px] font-semibold'
                textAnchor='middle'
                x={NODE_WIDTH / 2}
                y='28'
              >
                {compactLabel(node.key)}
              </text>
              <text
                className='fill-muted-foreground text-[11px] uppercase'
                textAnchor='middle'
                x={NODE_WIDTH / 2}
                y='48'
              >
                {compactLabel(`${node.node_type} / ${statusLabel(node.status)}`)}
              </text>
            </g>
          )

          if (node.node_type !== 'ticket') {
            return frame
          }

          return (
            <a
              aria-label={`Open ${node.key}`}
              data-testid={`dependency-node-link-${node.key}`}
              href={ticketDetailHref(node.key)}
              key={node.key}
              tabIndex={0}
            >
              {frame}
            </a>
          )
        })}
      </g>
    </svg>
  )
}

export function DependencyGraphView({
  criticalPath,
  graph,
}: {
  criticalPath: DependencyCriticalPathResponse
  graph: DependencyGraphResponse
}) {
  const [showTerminalStatuses, setShowTerminalStatuses] = useState(false)
  const layout = useMemo(
    () =>
      buildDependencyGraphLayout(graph, {
        criticalPathKeys: criticalPath.keys,
        showTerminalStatuses,
      }),
    [criticalPath.keys, graph, showTerminalStatuses]
  )

  if (graph.nodes.length === 0) {
    return (
      <Main>
        <EmptyCollectionState
          detail='The dependency graph route returned an empty projection.'
          title='No dependency graph nodes'
        />
      </Main>
    )
  }

  return (
    <Main fluid>
      <div className='flex flex-col gap-5' data-testid='dependency-graph-route'>
        <div className='flex flex-col gap-4 border-b pb-5 lg:flex-row lg:items-end lg:justify-between'>
          <div className='space-y-1'>
            <p className='text-muted-foreground text-sm font-medium'>
              Dependencies
            </p>
            <h1 className='text-2xl font-semibold tracking-normal'>
              Dependency Graph
            </h1>
          </div>
          <div className='flex flex-wrap items-center gap-2'>
            <Badge className='gap-1.5' variant='outline'>
              <Network aria-hidden='true' />
              No render cap
            </Badge>
            <Badge className='gap-1.5' variant='secondary'>
              <GitBranch aria-hidden='true' />
              {criticalPath.keys.length} critical path nodes
            </Badge>
            <Button
              aria-pressed={showTerminalStatuses}
              onClick={() => setShowTerminalStatuses((current) => !current)}
              type='button'
              variant='outline'
            >
              {showTerminalStatuses ? (
                <EyeOff aria-hidden='true' />
              ) : (
                <Eye aria-hidden='true' />
              )}
              {showTerminalStatuses
                ? 'Hide terminal statuses'
                : 'Show terminal statuses'}
            </Button>
          </div>
        </div>

        <dl className='grid gap-3 sm:grid-cols-2 xl:grid-cols-4'>
          <GraphStat
            label='Nodes'
            value={`${layout.nodes.length} of ${layout.totalNodeCount}`}
          />
          <GraphStat
            label='Edges'
            value={`${layout.edges.length} of ${layout.totalEdgeCount}`}
          />
          <GraphStat
            label='Terminal'
            value={
              layout.hiddenTerminalCount > 0
                ? `${layout.hiddenTerminalCount} terminal hidden`
                : 'Terminal shown'
            }
          />
          <GraphStat
            label='Critical Path'
            value={`${criticalPath.total_effort} effort`}
          />
        </dl>

        <DependencyGraphCanvas layout={layout} />
      </div>
    </Main>
  )
}

export function DependencyGraphRoute() {
  const graphQuery = useDependencyGraphQuery()
  const criticalPathQuery = useDependencyCriticalPathQuery()

  if (graphQuery.isLoading || criticalPathQuery.isLoading) {
    return (
      <Main>
        <LoadingState label='Loading dependency graph' />
      </Main>
    )
  }

  if (graphQuery.isError) {
    return (
      <Main>
        <RequestErrorState
          error={graphQuery.error}
          title='Dependency graph request failed'
        />
      </Main>
    )
  }

  if (criticalPathQuery.isError) {
    return (
      <Main>
        <RequestErrorState
          error={criticalPathQuery.error}
          title='Critical path request failed'
        />
      </Main>
    )
  }

  if (!graphQuery.data || !criticalPathQuery.data) {
    return (
      <Main>
        <LoadingState label='Loading dependency graph' />
      </Main>
    )
  }

  return (
    <DependencyGraphView
      criticalPath={criticalPathQuery.data}
      graph={graphQuery.data}
    />
  )
}
