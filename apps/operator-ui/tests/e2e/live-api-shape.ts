import type { components, paths } from '../../src/api/atlas-openapi'

type Schema = components['schemas']
type GeneratedGetRoute = {
  [Path in keyof paths]: Exclude<paths[Path]['get'], undefined> extends never
    ? never
    : Path
}[keyof paths]
export type LiveApiGetRoute = Exclude<
  GeneratedGetRoute,
  '/api/v1/acceptance-sessions/{session_id}'
>
type GetOperation<Path extends LiveApiGetRoute> = Exclude<
  paths[Path]['get'],
  undefined
>
type JsonResponse<Operation> = Operation extends {
  responses: {
    200: {
      content: {
        'application/json': infer Response
      }
    }
  }
}
  ? Response
  : never
type RouteResponse<Path extends LiveApiGetRoute> = JsonResponse<
  GetOperation<Path>
>

type JsonObject = Record<string, unknown>

function assertObject(value: unknown, path: string): asserts value is JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${path} must be an object`)
  }
}

function assertExactKeys(
  value: JsonObject,
  expectedKeys: readonly string[],
  path: string
): void {
  const actual = Object.keys(value).sort()
  const expected = [...expectedKeys].sort()
  const missing = expected.filter((key) => !actual.includes(key))
  const unexpected = actual.filter((key) => !expected.includes(key))

  if (missing.length > 0 || unexpected.length > 0) {
    throw new Error(
      `${path} shape mismatch; missing [${missing.join(', ')}], unexpected [${unexpected.join(', ')}]`
    )
  }
}

function assertString(value: unknown, path: string): asserts value is string {
  if (typeof value !== 'string') {
    throw new Error(`${path} must be a string`)
  }
}

function assertNumber(value: unknown, path: string): asserts value is number {
  if (typeof value !== 'number') {
    throw new Error(`${path} must be a number`)
  }
}

function assertInteger(value: unknown, path: string): asserts value is number {
  assertNumber(value, path)
  if (!Number.isInteger(value)) {
    throw new Error(`${path} must be an integer`)
  }
}

function assertBoolean(value: unknown, path: string): asserts value is boolean {
  if (typeof value !== 'boolean') {
    throw new Error(`${path} must be a boolean`)
  }
}

function assertNullableString(value: unknown, path: string): void {
  if (value !== null) {
    assertString(value, path)
  }
}

function assertUuidString(value: unknown, path: string): asserts value is string {
  assertString(value, path)
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      value
    )
  ) {
    throw new Error(`${path} must be a UUID string`)
  }
}

function assertIsoDateTimeOrNull(value: unknown, path: string): void {
  if (value === null) {
    return
  }
  assertString(value, path)
  if (Number.isNaN(Date.parse(value))) {
    throw new Error(`${path} must be an ISO datetime`)
  }
}

function assertArray<T>(
  value: unknown,
  path: string,
  assertItem: (item: unknown, itemPath: string) => asserts item is T
): asserts value is T[] {
  if (!Array.isArray(value)) {
    throw new Error(`${path} must be an array`)
  }
  value.forEach((item, index) => assertItem(item, `${path}[${index}]`))
}

function assertStringArray(value: unknown, path: string): asserts value is string[] {
  assertArray(value, path, assertString)
}

function assertUuidStringArray(
  value: unknown,
  path: string
): asserts value is string[] {
  assertArray(value, path, assertUuidString)
}

function assertTicketBoardItem(
  value: unknown,
  path: string
): asserts value is RouteResponse<'/api/v1/tickets'>['tickets'][number] {
  assertObject(value, path)
  assertExactKeys(
    value,
    ['epic_key', 'key', 'priority', 'risk_level', 'status', 'ticket_type', 'title'],
    path
  )
  assertString(value.key, `${path}.key`)
  assertString(value.title, `${path}.title`)
  assertString(value.status, `${path}.status`)
  assertString(value.ticket_type, `${path}.ticket_type`)
  assertInteger(value.priority, `${path}.priority`)
  assertString(value.risk_level, `${path}.risk_level`)
  assertNullableString(value.epic_key, `${path}.epic_key`)
}

export function assertTicketBoardResponse(
  value: unknown
): asserts value is RouteResponse<'/api/v1/tickets'> {
  assertObject(value, 'TicketBoardResponse')
  assertExactKeys(value, ['tickets'], 'TicketBoardResponse')
  assertArray(value.tickets, 'TicketBoardResponse.tickets', assertTicketBoardItem)
}

export function assertTicketDetailResponse(
  value: unknown
): asserts value is RouteResponse<'/api/v1/tickets/{key}'> {
  assertObject(value, 'TicketDetailResponse')
  assertExactKeys(
    value,
    [
      'acceptance_criteria',
      'completed_at',
      'component',
      'context',
      'created_at',
      'definition_of_done',
      'documentation_requirements',
      'estimated_effort',
      'external_github_issue_id',
      'external_linear_id',
      'implementation_notes',
      'key',
      'non_goals',
      'objective',
      'priority',
      'relevant_docs',
      'risk_level',
      'source_anchor',
      'status',
      'tags',
      'test_requirements',
      'ticket_type',
      'title',
      'updated_at',
    ],
    'TicketDetailResponse'
  )
  assertString(value.key, 'TicketDetailResponse.key')
  assertString(value.title, 'TicketDetailResponse.title')
  assertString(value.objective, 'TicketDetailResponse.objective')
  assertString(value.context, 'TicketDetailResponse.context')
  assertString(value.status, 'TicketDetailResponse.status')
  assertString(value.ticket_type, 'TicketDetailResponse.ticket_type')
  assertString(value.risk_level, 'TicketDetailResponse.risk_level')
  assertInteger(value.priority, 'TicketDetailResponse.priority')
  if (value.estimated_effort !== null) {
    assertInteger(value.estimated_effort, 'TicketDetailResponse.estimated_effort')
  }
  assertStringArray(value.relevant_docs, 'TicketDetailResponse.relevant_docs')
  assertStringArray(
    value.acceptance_criteria,
    'TicketDetailResponse.acceptance_criteria'
  )
  assertStringArray(value.non_goals, 'TicketDetailResponse.non_goals')
  assertStringArray(
    value.implementation_notes,
    'TicketDetailResponse.implementation_notes'
  )
  assertStringArray(
    value.test_requirements,
    'TicketDetailResponse.test_requirements'
  )
  assertStringArray(
    value.documentation_requirements,
    'TicketDetailResponse.documentation_requirements'
  )
  assertStringArray(
    value.definition_of_done,
    'TicketDetailResponse.definition_of_done'
  )
  assertStringArray(value.tags, 'TicketDetailResponse.tags')
  assertNullableString(value.component, 'TicketDetailResponse.component')
  assertNullableString(
    value.external_linear_id,
    'TicketDetailResponse.external_linear_id'
  )
  assertNullableString(
    value.external_github_issue_id,
    'TicketDetailResponse.external_github_issue_id'
  )
  assertString(value.source_anchor, 'TicketDetailResponse.source_anchor')
  assertIsoDateTimeOrNull(value.created_at, 'TicketDetailResponse.created_at')
  assertIsoDateTimeOrNull(value.updated_at, 'TicketDetailResponse.updated_at')
  assertIsoDateTimeOrNull(value.completed_at, 'TicketDetailResponse.completed_at')
}

function assertTicketEvidenceItem(
  value: unknown,
  path: string
): asserts value is RouteResponse<'/api/v1/tickets/{key}/evidence'>['evidence'][number] {
  assertObject(value, path)
  assertExactKeys(value, ['has_system_pin_triple', 'status', 'tier', 'type'], path)
  assertString(value.type, `${path}.type`)
  assertString(value.tier, `${path}.tier`)
  assertString(value.status, `${path}.status`)
  assertBoolean(value.has_system_pin_triple, `${path}.has_system_pin_triple`)
}

export function assertTicketEvidenceResponse(
  value: unknown
): asserts value is RouteResponse<'/api/v1/tickets/{key}/evidence'> {
  assertObject(value, 'TicketEvidenceResponse')
  assertExactKeys(value, ['evidence'], 'TicketEvidenceResponse')
  assertArray(
    value.evidence,
    'TicketEvidenceResponse.evidence',
    assertTicketEvidenceItem
  )
}

function assertNotReadyReason(
  value: unknown,
  path: string
): asserts value is RouteResponse<'/api/v1/tickets/{key}/dependencies'>['readiness']['reasons'][number] {
  assertObject(value, path)
  assertExactKeys(value, ['code', 'message', 'status', 'target'], path)
  assertString(value.code, `${path}.code`)
  assertString(value.message, `${path}.message`)
  assertNullableString(value.target, `${path}.target`)
  assertNullableString(value.status, `${path}.status`)
}

function assertDependencyBlocker(
  value: unknown,
  path: string
): asserts value is RouteResponse<'/api/v1/tickets/{key}/dependencies'>['blockers'][number] {
  assertObject(value, path)
  assertExactKeys(value, ['code', 'key'], path)
  assertString(value.key, `${path}.key`)
  assertString(value.code, `${path}.code`)
}

export function assertTicketDependenciesResponse(
  value: unknown
): asserts value is RouteResponse<'/api/v1/tickets/{key}/dependencies'> {
  assertObject(value, 'TicketDependenciesResponse')
  assertExactKeys(
    value,
    ['blocked_by', 'blockers', 'key', 'readiness'],
    'TicketDependenciesResponse'
  )
  assertString(value.key, 'TicketDependenciesResponse.key')
  assertArray(
    value.blockers,
    'TicketDependenciesResponse.blockers',
    assertDependencyBlocker
  )
  assertStringArray(value.blocked_by, 'TicketDependenciesResponse.blocked_by')
  assertObject(value.readiness, 'TicketDependenciesResponse.readiness')
  assertExactKeys(
    value.readiness,
    ['ready', 'reasons'],
    'TicketDependenciesResponse.readiness'
  )
  assertBoolean(
    value.readiness.ready,
    'TicketDependenciesResponse.readiness.ready'
  )
  assertArray(
    value.readiness.reasons,
    'TicketDependenciesResponse.readiness.reasons',
    assertNotReadyReason
  )
}

function assertEpicItem(
  value: unknown,
  path: string
): asserts value is RouteResponse<'/api/v1/epics'>['epics'][number] {
  assertObject(value, path)
  assertExactKeys(
    value,
    [
      'completed_at',
      'created_at',
      'created_by_id',
      'created_by_type',
      'description',
      'id',
      'key',
      'objective',
      'priority',
      'product_id',
      'risk_level',
      'source_anchor',
      'status',
      'title',
      'updated_at',
    ],
    path
  )
  assertUuidString(value.id, `${path}.id`)
  assertUuidString(value.product_id, `${path}.product_id`)
  assertString(value.key, `${path}.key`)
  assertString(value.title, `${path}.title`)
  assertString(value.description, `${path}.description`)
  assertString(value.objective, `${path}.objective`)
  assertString(value.status, `${path}.status`)
  assertInteger(value.priority, `${path}.priority`)
  assertString(value.risk_level, `${path}.risk_level`)
  assertString(value.source_anchor, `${path}.source_anchor`)
  assertString(value.created_by_type, `${path}.created_by_type`)
  assertString(value.created_by_id, `${path}.created_by_id`)
  assertIsoDateTimeOrNull(value.created_at, `${path}.created_at`)
  assertIsoDateTimeOrNull(value.updated_at, `${path}.updated_at`)
  assertIsoDateTimeOrNull(value.completed_at, `${path}.completed_at`)
}

function assertEpicsResponse(
  value: unknown
): asserts value is RouteResponse<'/api/v1/epics'> {
  assertObject(value, 'EpicsResponse')
  assertExactKeys(value, ['epics'], 'EpicsResponse')
  assertArray(value.epics, 'EpicsResponse.epics', assertEpicItem)
}

function assertLessonItem(
  value: unknown,
  path: string
): asserts value is RouteResponse<'/api/v1/lessons'>['lessons'][number] {
  assertObject(value, path)
  assertExactKeys(
    value,
    [
      'category',
      'confidence',
      'created_at',
      'created_by_id',
      'created_by_type',
      'id',
      'outcome',
      'problem',
      'product_id',
      'related_adr_ids',
      'related_ticket_ids',
      'solution',
      'source_ticket_id',
      'status',
      'tags',
      'title',
      'updated_at',
    ],
    path
  )
  assertUuidString(value.id, `${path}.id`)
  assertUuidString(value.product_id, `${path}.product_id`)
  assertString(value.status, `${path}.status`)
  assertString(value.category, `${path}.category`)
  assertString(value.title, `${path}.title`)
  assertString(value.problem, `${path}.problem`)
  assertString(value.solution, `${path}.solution`)
  assertString(value.outcome, `${path}.outcome`)
  if (value.confidence !== null) {
    assertNumber(value.confidence, `${path}.confidence`)
  }
  assertUuidString(value.source_ticket_id, `${path}.source_ticket_id`)
  assertUuidStringArray(value.related_ticket_ids, `${path}.related_ticket_ids`)
  assertUuidStringArray(value.related_adr_ids, `${path}.related_adr_ids`)
  assertStringArray(value.tags, `${path}.tags`)
  assertString(value.created_by_type, `${path}.created_by_type`)
  assertString(value.created_by_id, `${path}.created_by_id`)
  assertIsoDateTimeOrNull(value.created_at, `${path}.created_at`)
  assertIsoDateTimeOrNull(value.updated_at, `${path}.updated_at`)
}

export function assertLessonsResponse(
  value: unknown
): asserts value is RouteResponse<'/api/v1/lessons'> {
  assertObject(value, 'LessonsResponse')
  assertExactKeys(value, ['lessons'], 'LessonsResponse')
  assertArray(value.lessons, 'LessonsResponse.lessons', assertLessonItem)
}

function assertCriticalPathStep(
  value: unknown,
  path: string
): asserts value is RouteResponse<'/api/v1/dependencies/critical-path'>['steps'][number] {
  assertObject(value, path)
  assertExactKeys(value, ['cumulative_effort', 'effort', 'key'], path)
  assertString(value.key, `${path}.key`)
  assertInteger(value.effort, `${path}.effort`)
  assertInteger(value.cumulative_effort, `${path}.cumulative_effort`)
}

export function assertDependencyCriticalPathResponse(
  value: unknown
): asserts value is RouteResponse<'/api/v1/dependencies/critical-path'> {
  assertObject(value, 'DependencyCriticalPathResponse')
  assertExactKeys(
    value,
    ['keys', 'steps', 'total_effort'],
    'DependencyCriticalPathResponse'
  )
  assertStringArray(value.keys, 'DependencyCriticalPathResponse.keys')
  assertArray(
    value.steps,
    'DependencyCriticalPathResponse.steps',
    assertCriticalPathStep
  )
  assertInteger(value.total_effort, 'DependencyCriticalPathResponse.total_effort')
}

function assertDependencyGraphNode(
  value: unknown,
  path: string
): asserts value is RouteResponse<'/api/v1/dependencies/graph'>['nodes'][number] {
  assertObject(value, path)
  assertExactKeys(value, ['key', 'node_type', 'status'], path)
  assertString(value.key, `${path}.key`)
  assertString(value.status, `${path}.status`)
  assertString(value.node_type, `${path}.node_type`)
}

function assertDependencyGraphEdge(
  value: unknown,
  path: string
): asserts value is RouteResponse<'/api/v1/dependencies/graph'>['edges'][number] {
  assertObject(value, path)
  assertExactKeys(value, ['dependency_type', 'source', 'target'], path)
  assertString(value.source, `${path}.source`)
  assertString(value.target, `${path}.target`)
  assertString(value.dependency_type, `${path}.dependency_type`)
}

export function assertDependencyGraphResponse(
  value: unknown
): asserts value is RouteResponse<'/api/v1/dependencies/graph'> {
  assertObject(value, 'DependencyGraphResponse')
  assertExactKeys(value, ['edges', 'nodes'], 'DependencyGraphResponse')
  assertArray(
    value.nodes,
    'DependencyGraphResponse.nodes',
    assertDependencyGraphNode
  )
  assertArray(
    value.edges,
    'DependencyGraphResponse.edges',
    assertDependencyGraphEdge
  )
}

function assertReviewCheck(
  value: unknown,
  path: string
): asserts value is Schema['ReviewCheckSchema'] {
  assertObject(value, path)
  assertExactKeys(value, ['check_type', 'status'], path)
  assertString(value.check_type, `${path}.check_type`)
  assertString(value.status, `${path}.status`)
}

function assertReviewItem(
  value: unknown,
  path: string
): asserts value is RouteResponse<'/api/v1/reviews'>['reviews'][number] {
  assertObject(value, path)
  assertExactKeys(
    value,
    [
      'checks',
      'has_pr_merged_evidence',
      'has_system_evidence',
      'key',
      'status',
      'ticket_type',
      'title',
      'verdict',
    ],
    path
  )
  assertString(value.key, `${path}.key`)
  assertString(value.title, `${path}.title`)
  assertString(value.status, `${path}.status`)
  assertString(value.ticket_type, `${path}.ticket_type`)
  assertString(value.verdict, `${path}.verdict`)
  assertArray(value.checks, `${path}.checks`, assertReviewCheck)
  assertBoolean(value.has_system_evidence, `${path}.has_system_evidence`)
  assertBoolean(
    value.has_pr_merged_evidence,
    `${path}.has_pr_merged_evidence`
  )
}

export function assertReviewQueueResponse(
  value: unknown
): asserts value is RouteResponse<'/api/v1/reviews'> {
  assertObject(value, 'ReviewQueueResponse')
  assertExactKeys(value, ['reviews'], 'ReviewQueueResponse')
  assertArray(value.reviews, 'ReviewQueueResponse.reviews', assertReviewItem)
}

function assertTicketCountResponse(
  value: unknown
): asserts value is RouteResponse<'/api/v1/tickets/count'> {
  assertObject(value, 'TicketCountResponse')
  assertExactKeys(value, ['count'], 'TicketCountResponse')
  assertInteger(value.count, 'TicketCountResponse.count')
}

function assertSystemStatusResponse(
  value: unknown
): asserts value is RouteResponse<'/api/v1/status'> {
  assertObject(value, 'SystemStatusResponse')
  assertExactKeys(
    value,
    [
      'evidence_count',
      'last_evidence_pull_at',
      'last_linear_sync_at',
      'package_version',
      'schema_revision',
      'ticket_count',
    ],
    'SystemStatusResponse'
  )
  assertString(value.package_version, 'SystemStatusResponse.package_version')
  assertNullableString(value.schema_revision, 'SystemStatusResponse.schema_revision')
  assertInteger(value.ticket_count, 'SystemStatusResponse.ticket_count')
  assertInteger(value.evidence_count, 'SystemStatusResponse.evidence_count')
  assertIsoDateTimeOrNull(
    value.last_linear_sync_at,
    'SystemStatusResponse.last_linear_sync_at'
  )
  assertIsoDateTimeOrNull(
    value.last_evidence_pull_at,
    'SystemStatusResponse.last_evidence_pull_at'
  )
}

function assertSessionStateResponse(
  value: unknown
): asserts value is RouteResponse<'/api/v1/session'> {
  assertObject(value, 'SessionStateResponse')
  assertExactKeys(value, ['authenticated', 'expires_at'], 'SessionStateResponse')
  assertBoolean(value.authenticated, 'SessionStateResponse.authenticated')
  assertIsoDateTimeOrNull(value.expires_at, 'SessionStateResponse.expires_at')
}

export function assertDeliveryControlResponse(
  value: unknown
): asserts value is RouteResponse<'/api/v1/delivery-control'> {
  assertObject(value, 'DeliveryControlResponse')
  assertExactKeys(
    value,
    [
      'indeterminate_reasons',
      'last_linear_sync_at',
      'latest_admission',
      'occupancy',
      'policy',
    ],
    'DeliveryControlResponse'
  )
  assertObject(value.policy, 'DeliveryControlResponse.policy')
  assertExactKeys(
    value.policy,
    [
      'approved_symphony_ceiling',
      'changes_requested_reserve',
      'component_lane_limits',
      'created_at',
      'id',
      'mode',
      'review_budget',
      'revision',
      'risk_lane_limits',
      'working_budget',
    ],
    'DeliveryControlResponse.policy'
  )
  assertUuidString(value.policy.id, 'DeliveryControlResponse.policy.id')
  assertInteger(value.policy.revision, 'DeliveryControlResponse.policy.revision')
  assertString(value.policy.mode, 'DeliveryControlResponse.policy.mode')
  assertInteger(
    value.policy.approved_symphony_ceiling,
    'DeliveryControlResponse.policy.approved_symphony_ceiling'
  )
  assertInteger(
    value.policy.working_budget,
    'DeliveryControlResponse.policy.working_budget'
  )
  assertInteger(
    value.policy.review_budget,
    'DeliveryControlResponse.policy.review_budget'
  )
  assertInteger(
    value.policy.changes_requested_reserve,
    'DeliveryControlResponse.policy.changes_requested_reserve'
  )
  assertArray(
    value.policy.risk_lane_limits,
    'DeliveryControlResponse.policy.risk_lane_limits',
    (item: unknown, path: string): asserts item is JsonObject => {
      assertObject(item, path)
    }
  )
  assertArray(
    value.policy.component_lane_limits,
    'DeliveryControlResponse.policy.component_lane_limits',
    (item: unknown, path: string): asserts item is JsonObject => {
      assertObject(item, path)
    }
  )
  assertIsoDateTimeOrNull(
    value.policy.created_at,
    'DeliveryControlResponse.policy.created_at'
  )
  assertIsoDateTimeOrNull(
    value.last_linear_sync_at,
    'DeliveryControlResponse.last_linear_sync_at'
  )
  assertObject(value.occupancy, 'DeliveryControlResponse.occupancy')
  assertExactKeys(
    value.occupancy,
    [
      'changes_requested_occupancy',
      'changes_requested_reserve_remaining',
      'component_lane_occupancy',
      'new_admission_working_capacity',
      'over_capacity_reasons',
      'review_occupancy',
      'risk_lane_occupancy',
      'source',
      'status_occupancy',
      'working_occupancy',
    ],
    'DeliveryControlResponse.occupancy'
  )
  assertString(value.occupancy.source, 'DeliveryControlResponse.occupancy.source')
  assertInteger(
    value.occupancy.working_occupancy,
    'DeliveryControlResponse.occupancy.working_occupancy'
  )
  assertInteger(
    value.occupancy.review_occupancy,
    'DeliveryControlResponse.occupancy.review_occupancy'
  )
  assertArray(
    value.occupancy.status_occupancy,
    'DeliveryControlResponse.occupancy.status_occupancy',
    (item: unknown, path: string): asserts item is JsonObject => {
      assertObject(item, path)
    }
  )
  assertArray(
    value.occupancy.over_capacity_reasons,
    'DeliveryControlResponse.occupancy.over_capacity_reasons',
    (item: unknown, path: string): asserts item is JsonObject => {
      assertObject(item, path)
    }
  )
  assertArray(
    value.indeterminate_reasons,
    'DeliveryControlResponse.indeterminate_reasons',
    (item: unknown, path: string): asserts item is JsonObject => {
      assertObject(item, path)
    }
  )
}

export const liveApiShapeAssertions = {
  '/api/v1/delivery-control': assertDeliveryControlResponse,
  '/api/v1/tickets': assertTicketBoardResponse,
  '/api/v1/tickets/count': assertTicketCountResponse,
  '/api/v1/tickets/{key}': assertTicketDetailResponse,
  '/api/v1/tickets/{key}/evidence': assertTicketEvidenceResponse,
  '/api/v1/tickets/{key}/dependencies': assertTicketDependenciesResponse,
  '/api/v1/epics': assertEpicsResponse,
  '/api/v1/lessons': assertLessonsResponse,
  '/api/v1/dependencies/critical-path': assertDependencyCriticalPathResponse,
  '/api/v1/dependencies/graph': assertDependencyGraphResponse,
  '/api/v1/reviews': assertReviewQueueResponse,
  '/api/v1/session': assertSessionStateResponse,
  '/api/v1/status': assertSystemStatusResponse,
} satisfies {
  [Path in LiveApiGetRoute]: (
    value: unknown
  ) => asserts value is RouteResponse<Path>
}
