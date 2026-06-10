# ADR-0009: Single-operator governance

## Status

Accepted

## Context

Atlas will be operated by one human for the foreseeable future. Several
design questions (team and permission models, approval routing, review
assignment) are expensive to solve generally and cheap to solve for one
operator, but the schema should not preclude multi-user operation later.

## Decision

- Atlas assumes exactly one human operator, identified as
  `created_by_type: human, created_by_id: "operator"`. No team, role, or
  permission model is built.
- All human gates resolve to the operator: plan approval (`atlas apply`),
  `MANUAL_APPROVAL` verification checks, `NEEDS_HUMAN_DECISION` tickets,
  and lesson promotion.
- **Lesson promotion gate.** Agent-authored lessons are created with
  `status: DRAFT` and are excluded from context-pack retrieval until the
  operator promotes them to `ACTIVE`. This closes the loop-poisoning path
  where an agent writes a bad lesson that is then injected into every
  future context pack. The `Lesson` model gains a `status: EntityStatus`
  field; retrieval filters on `ACTIVE`.
- Actor attribution (`created_by_type` / `created_by_id`) is retained on
  every record exactly as specified, so multi-user support is an additive
  change (introduce identities and routing) rather than a migration.

## Rationale

Single-operator is the honest current state; building permissions now would
be speculative scope. The one governance mechanism that cannot wait is the
lesson gate, because the Learning System feeds the Context Renderer and an
ungated write path into organisational memory compounds errors.

## Consequences

- Phases 1–9 carry no auth, team, or permission tickets.
- The operator is a serial bottleneck on plan approval and verification
  sign-off; this is accepted and is the correct place for human attention
  per the harness-engineering division of labour.
- Revisit this ADR before any second human or any externally hosted
  deployment.

## Alternatives considered

- Minimal two-role model now: rejected as speculative.
- No lesson gate (trust agent lessons): rejected; highest-leverage
  self-corruption risk in the design.
