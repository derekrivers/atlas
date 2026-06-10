# WORKFLOW.md

## Current Workflow

Development begins locally. No Linear or Symphony automation is introduced
until the local planning loop works.

## First Workflow

1. Edit Atlas docs (intent).
2. Run `atlas plan` — LLM proposal, validation gates, reconciled diff.
3. Review the diff.
4. Run `atlas apply` — keys assigned, renders written, PlanRun recorded.
5. Commit docs and generated planning output together.

## Future Workflow

```text
Docs → Plan/Apply → Dependency Graph → Linear → Context Pack → Symphony
→ PR → CI Evidence → Verification → Lesson (DRAFT → promoted) → Docs
```

Field ownership in the Linear stage follows ADR-0006: definitions flow
Atlas → Linear; status flows Linear → Atlas.
