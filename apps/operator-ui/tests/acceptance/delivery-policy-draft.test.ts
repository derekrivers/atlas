import { describe, expect, it } from 'vitest'
import type { components } from '@/api/atlas-openapi'
import {
  newComponentLane,
  newRiskLane,
  policyDraftFromPolicy,
  validatePolicyDraft,
} from '@/features/delivery-control/policy-draft'

type Policy = components['schemas']['DeliveryAdmissionPolicySchema']

const policy: Policy = {
  approved_symphony_ceiling: 3,
  changes_requested_reserve: 1,
  component_lane_limits: [{ component: 'operator-ui', limit: 2 }],
  created_at: '2026-08-13T10:00:00Z',
  id: '00000000-0000-4000-8000-000000000251',
  mode: 'running',
  review_budget: 2,
  revision: 7,
  risk_lane_limits: [{ risk_level: 'high', limit: 2 }],
  working_budget: 3,
}

describe('complete delivery policy proposal', () => {
  it('copies every server policy field and expected revision into one full request', () => {
    const result = validatePolicyDraft(policyDraftFromPolicy(policy))

    expect(result.errors).toEqual({})
    expect(result.proposal).toEqual({
      approved_symphony_ceiling: 3,
      changes_requested_reserve: 1,
      component_lane_limits: [{ component: 'operator-ui', limit: 2 }],
      expected_revision: 7,
      mode: 'running',
      review_budget: 2,
      risk_lane_limits: [{ risk_level: 'high', limit: 2 }],
      working_budget: 3,
    })
  })

  it('rejects incoherent bounds and duplicate lane selectors without adjusting them', () => {
    const draft = policyDraftFromPolicy(policy)
    draft.approvedPolicyCeiling = '1'
    draft.workingBudget = '2'
    draft.changesRequestedReserve = '3'
    draft.riskLanes.push(newRiskLane('high'))
    const duplicate = newComponentLane()
    duplicate.component = ' OPERATOR-UI '
    duplicate.limit = '3'
    draft.componentLanes.push(duplicate)

    const result = validatePolicyDraft(draft)

    expect(result.proposal).toBeNull()
    expect(Object.values(result.errors)).toEqual(
      expect.arrayContaining([
        'Working budget cannot exceed the approved policy ceiling.',
        'Changes Requested reserve cannot exceed the working budget.',
        'Each risk level may appear only once.',
        'Component selectors must be unique after canonicalisation.',
        'Component lane limit cannot exceed the working budget.',
      ])
    )
    expect(draft.approvedPolicyCeiling).toBe('1')
    expect(draft.workingBudget).toBe('2')
    expect(draft.componentLanes[1].component).toBe(' OPERATOR-UI ')
  })
})
