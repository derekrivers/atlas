import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import {
  assertDependencyCriticalPathResponse,
  assertDependencyGraphResponse,
} from './live-api-shape'
import { startAtlasApiServer } from './atlas-api-server'
import { isTerminalTicketStatus } from '../../src/features/tickets/ticket-board-state'

const apiBaseURL =
  process.env.ATLAS_OPERATOR_E2E_API_URL ?? 'http://127.0.0.1:18000'

let apiServer: Awaited<ReturnType<typeof startAtlasApiServer>> | undefined

test.beforeAll(async () => {
  apiServer = await startAtlasApiServer()
})

test.afterAll(async () => {
  await apiServer?.stop()
  apiServer = undefined
})

async function getJson(request: APIRequestContext, path: string): Promise<unknown> {
  const response = await request.get(`${apiBaseURL}${path}`)
  expect(response.ok(), `${path} should return 2xx`).toBe(true)
  return response.json()
}

function nodeSelector(key: string): string {
  return `[data-node-key="${key.replaceAll('"', '\\"')}"]`
}

async function visibleInteractiveLabels(page: Page): Promise<string[]> {
  return page
    .locator(
      [
        'a',
        'button',
        '[role="button"]',
        '[role="link"]',
        '[role="menuitem"]',
        '[data-slot="command-item"]',
      ].join(',')
    )
    .evaluateAll((elements) =>
      elements
        .filter((element) => {
          const style = window.getComputedStyle(element)
          const box = element.getBoundingClientRect()
          return (
            style.visibility !== 'hidden' &&
            style.display !== 'none' &&
            box.width > 0 &&
            box.height > 0
          )
        })
        .map((element) =>
          [
            element.getAttribute('aria-label'),
            element.textContent,
            element.getAttribute('title'),
          ]
            .filter(Boolean)
            .join(' ')
            .replace(/\s+/g, ' ')
            .trim()
        )
        .filter(Boolean)
    )
}

test('dependency graph renders the complete API projection once terminal nodes are revealed', async ({
  page,
  request,
}) => {
  const graph = await getJson(request, '/api/v1/dependencies/graph')
  assertDependencyGraphResponse(graph)
  const terminalNodes = graph.nodes.filter(
    (node) => node.node_type === 'ticket' && isTerminalTicketStatus(node.status)
  )
  const defaultVisibleKeys = new Set(
    graph.nodes
      .filter((node) => !terminalNodes.some((hidden) => hidden.key === node.key))
      .map((node) => node.key)
  )
  const defaultVisibleEdges = graph.edges.filter(
    (edge) =>
      defaultVisibleKeys.has(edge.source) && defaultVisibleKeys.has(edge.target)
  )

  await page.goto('/dependency-graph')

  await expect(
    page.getByRole('heading', { exact: true, name: 'Dependency Graph' })
  ).toBeVisible()
  await expect(page.getByText('No render cap')).toBeVisible()
  await expect(page.getByText(`${terminalNodes.length} terminal hidden`)).toBeVisible()
  await expect(page.getByTestId('dependency-graph-node')).toHaveCount(
    defaultVisibleKeys.size
  )
  await expect(page.getByTestId('dependency-graph-edge')).toHaveCount(
    defaultVisibleEdges.length
  )

  for (const node of terminalNodes) {
    await expect(page.locator(nodeSelector(node.key))).toHaveCount(0)
  }

  await page.getByRole('button', { name: 'Show terminal statuses' }).click()

  await expect(page.getByText('Terminal shown')).toBeVisible()
  await expect(page.getByTestId('dependency-graph-node')).toHaveCount(
    graph.nodes.length
  )
  await expect(page.getByTestId('dependency-graph-edge')).toHaveCount(
    graph.edges.length
  )
})

test('dependency graph highlights the critical path and keeps edges read-only', async ({
  page,
  request,
}) => {
  const graph = await getJson(request, '/api/v1/dependencies/graph')
  assertDependencyGraphResponse(graph)
  const criticalPath = await getJson(
    request,
    '/api/v1/dependencies/critical-path'
  )
  assertDependencyCriticalPathResponse(criticalPath)
  expect(criticalPath.keys.length).toBeGreaterThan(0)

  await page.goto('/dependency-graph')

  for (const key of criticalPath.keys) {
    const node = page.locator(nodeSelector(key))
    await expect(node).toHaveAttribute('data-critical', 'true')
    await expect(node.getByTestId('dependency-graph-node-frame')).toHaveAttribute(
      'class',
      /stroke-primary/
    )
  }

  const ticket = graph.nodes.find(
    (node) => node.node_type === 'ticket' && !isTerminalTicketStatus(node.status)
  )
  if (!ticket) {
    throw new Error('seed should include a visible ticket node')
  }

  const ticketLink = page.getByTestId(`dependency-node-link-${ticket.key}`)
  await expect(ticketLink).toHaveAttribute('href', `/tickets/${ticket.key}`)

  const labels = await visibleInteractiveLabels(page)
  expect(
    labels.filter((label) =>
      /\b(add|create|delete|remove|reparent|mutate)\s+(edge|dependency)\b|\b(edge|dependency)\s+(add|create|delete|remove|reparent|mutate)\b/i.test(
        label
      )
    )
  ).toEqual([])

  await ticketLink.click()
  await expect(page).toHaveURL(new RegExp(`/tickets/${ticket.key}$`))
})
