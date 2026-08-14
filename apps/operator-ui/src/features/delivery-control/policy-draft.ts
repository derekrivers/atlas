import type { components } from '@/api/atlas-openapi'
import { atlasOpenApiEnums } from '@/api/atlas-openapi-runtime'

type Schema = components['schemas']
type Policy = Schema['DeliveryAdmissionPolicySchema']
type PolicyRequest = Schema['DeliveryAdmissionPolicyRequest']
type Mode = Schema['DeliveryAdmissionMode']
type RiskLevel = Schema['RiskLevel']

export type RiskLaneDraft = {
  id: string
  limit: string
  riskLevel: RiskLevel
}

export type ComponentLaneDraft = {
  component: string
  id: string
  limit: string
}

export type PolicyDraft = {
  approvedPolicyCeiling: string
  changesRequestedReserve: string
  componentLanes: ComponentLaneDraft[]
  expectedRevision: number
  mode: Mode
  reviewBudget: string
  riskLanes: RiskLaneDraft[]
  workingBudget: string
}

export type PolicyDraftValidation =
  | { errors: Record<string, string>; proposal: null }
  | { errors: Record<string, never>; proposal: PolicyRequest }

let rowSequence = 0

function rowId(prefix: string): string {
  rowSequence += 1
  return `${prefix}-${rowSequence}`
}

export function policyDraftFromPolicy(policy: Policy): PolicyDraft {
  return {
    approvedPolicyCeiling: String(policy.approved_symphony_ceiling),
    changesRequestedReserve: String(policy.changes_requested_reserve),
    componentLanes: policy.component_lane_limits.map((lane) => ({
      component: lane.component,
      id: rowId('component'),
      limit: String(lane.limit),
    })),
    expectedRevision: policy.revision,
    mode: policy.mode,
    reviewBudget: String(policy.review_budget),
    riskLanes: policy.risk_lane_limits.map((lane) => ({
      id: rowId('risk'),
      limit: String(lane.limit),
      riskLevel: lane.risk_level,
    })),
    workingBudget: String(policy.working_budget),
  }
}

export function newRiskLane(riskLevel: RiskLevel): RiskLaneDraft {
  return { id: rowId('risk'), limit: '0', riskLevel }
}

export function newComponentLane(): ComponentLaneDraft {
  return { component: '', id: rowId('component'), limit: '0' }
}

function strictInteger(
  value: string,
  field: string,
  minimum: number,
  maximum: number,
  errors: Record<string, string>
): number | null {
  if (!/^-?\d+$/.test(value.trim())) {
    errors[field] = 'Enter a whole number.'
    return null
  }
  const parsed = Number(value)
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
    errors[field] = `Enter a whole number from ${minimum} through ${maximum}.`
    return null
  }
  return parsed
}

function canonicalSelector(value: string): string {
  return value.normalize('NFKC').trim().toLocaleLowerCase()
}

export function validatePolicyDraft(draft: PolicyDraft): PolicyDraftValidation {
  const errors: Record<string, string> = {}
  const ceiling = strictInteger(
    draft.approvedPolicyCeiling,
    'approvedPolicyCeiling',
    1,
    10,
    errors
  )
  const working = strictInteger(
    draft.workingBudget,
    'workingBudget',
    1,
    10,
    errors
  )
  const review = strictInteger(
    draft.reviewBudget,
    'reviewBudget',
    1,
    10,
    errors
  )
  const reserve = strictInteger(
    draft.changesRequestedReserve,
    'changesRequestedReserve',
    0,
    10,
    errors
  )

  if (ceiling !== null && working !== null && working > ceiling) {
    errors.workingBudget = 'Working budget cannot exceed the approved policy ceiling.'
  }
  if (working !== null && reserve !== null && reserve > working) {
    errors.changesRequestedReserve =
      'Changes Requested reserve cannot exceed the working budget.'
  }
  if (draft.riskLanes.length > atlasOpenApiEnums.RiskLevel.length) {
    errors.riskLanes = `At most ${atlasOpenApiEnums.RiskLevel.length} risk lane limits may be configured.`
  }
  if (draft.componentLanes.length > 64) {
    errors.componentLanes = 'At most 64 component lane limits may be configured.'
  }

  const riskSelectors = new Set<RiskLevel>()
  const riskLaneLimits: PolicyRequest['risk_lane_limits'] = []
  for (const lane of draft.riskLanes) {
    if (riskSelectors.has(lane.riskLevel)) {
      errors[`risk-${lane.id}-selector`] = 'Each risk level may appear only once.'
    }
    riskSelectors.add(lane.riskLevel)
    const limit = strictInteger(
      lane.limit,
      `risk-${lane.id}-limit`,
      0,
      10,
      errors
    )
    if (limit !== null && working !== null && limit > working) {
      errors[`risk-${lane.id}-limit`] =
        'Risk lane limit cannot exceed the working budget.'
    }
    if (limit !== null) {
      riskLaneLimits.push({ limit, risk_level: lane.riskLevel })
    }
  }

  const componentSelectors = new Set<string>()
  const componentLaneLimits: PolicyRequest['component_lane_limits'] = []
  for (const lane of draft.componentLanes) {
    const canonical = canonicalSelector(lane.component)
    if (!canonical) {
      errors[`component-${lane.id}-selector`] = 'Enter a component selector.'
    } else if (canonical.length > 128) {
      errors[`component-${lane.id}-selector`] =
        'Component selectors may contain at most 128 characters.'
    } else if (componentSelectors.has(canonical)) {
      errors[`component-${lane.id}-selector`] =
        'Component selectors must be unique after canonicalisation.'
    }
    componentSelectors.add(canonical)
    const limit = strictInteger(
      lane.limit,
      `component-${lane.id}-limit`,
      0,
      10,
      errors
    )
    if (limit !== null && working !== null && limit > working) {
      errors[`component-${lane.id}-limit`] =
        'Component lane limit cannot exceed the working budget.'
    }
    if (canonical && canonical.length <= 128 && limit !== null) {
      componentLaneLimits.push({ component: lane.component, limit })
    }
  }

  if (
    Object.keys(errors).length > 0 ||
    ceiling === null ||
    working === null ||
    review === null ||
    reserve === null
  ) {
    return { errors, proposal: null }
  }

  return {
    errors: {},
    proposal: {
      approved_symphony_ceiling: ceiling,
      changes_requested_reserve: reserve,
      component_lane_limits: componentLaneLimits,
      expected_revision: draft.expectedRevision,
      mode: draft.mode,
      review_budget: review,
      risk_lane_limits: riskLaneLimits,
      working_budget: working,
    },
  }
}
