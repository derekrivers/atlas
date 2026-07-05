# Smoke B closeout — ATLAS-110

Captured: 2026-07-05T18:46:16+00:00
Repo: derekrivers/atlas · PR: #153 · head commit C: `b285f21c84e79a12e7605baf4411b251950776b6` · merged: True
Fixture: ATLAS-110 — "Document the delivery loop under docs/" · Linear issue: 4e120a70-968c-470f-8a44-f429cf34a1cb

## Seam checkpoints (ATLAS-143, observed live)
- 2.2 Linear title embeds the Atlas key: PASS — `ATLAS-110: Document the delivery loop under docs/`
- 3.2 PR title resolves to exactly (ATLAS-110,): PASS — `ATLAS-110: Document the delivery loop under docs/`

## Final states
- Linear: done (state 'Done')
- Atlas store: done

## Evidence rows (5)
- `793991b3-e319-4647-a870-a4ed37c03b09` manual_approval [passed] actor=human
- `89add3c8-51c5-4324-bbb4-e4838992f80d` manual_approval [passed] actor=human
- `8ae5a853-88a7-4577-821c-83c67f23628b` manual_approval [passed] actor=human
- `9a4371a9-3076-438a-8565-38eaa1b216fa` pr_merged [passed] commit=b285f21c84e79a12e7605baf4411b251950776b6 run=merge:b285f21c84e79a12e7605baf4411b251950776b6 hash=283f8ee3c14b87507d2b470463ea745f5a2ed3aa7c5b945dd22bc32890a2eca9
- `acec2d0e-5870-4791-a4c5-40e8db41418a` pr_merged [passed] commit=b285f21c84e79a12e7605baf4411b251950776b6 run=merge:b285f21c84e79a12e7605baf4411b251950776b6 hash=283f8ee3c14b87507d2b470463ea745f5a2ed3aa7c5b945dd22bc32890a2eca9

## Verification checks (12 append-only rows)
- documentation [passed] at 2026-07-05T18:36:07+00:00
- acceptance_criteria [pending] at 2026-07-05T18:36:07+00:00
- lint [passed] at 2026-07-05T18:36:07+00:00
- acceptance_criteria [passed] at 2026-07-05T18:40:02+00:00
- lint [passed] at 2026-07-05T18:40:02+00:00
- documentation [passed] at 2026-07-05T18:40:02+00:00
- documentation [passed] at 2026-07-05T18:42:48+00:00
- lint [passed] at 2026-07-05T18:42:48+00:00
- acceptance_criteria [passed] at 2026-07-05T18:42:48+00:00
- documentation [passed] at 2026-07-05T18:46:14+00:00
- lint [passed] at 2026-07-05T18:46:14+00:00
- acceptance_criteria [passed] at 2026-07-05T18:46:14+00:00

## Final verify report
```
Verification for derekrivers/atlas PR #153 at b285f21c84e79a12e7605baf4411b251950776b6
PR verdict: PASSED
  ATLAS-110: PASSED
    [PASSED        ] lint                 (required)  evidence: e37ad704-401f-4e55-878c-f827e855617e
      lint: system-tier lint_result evidence e37ad704-401f-4e55-878c-f827e855617e pinned to b285f21c84e79a12e7605baf4411b251950776b6 reports passed.
    [PASSED        ] acceptance_criteria  (required)  evidence: 793991b3-e319-4647-a870-a4ed37c03b09, 89add3c8-51c5-4324-bbb4-e4838992f80d, 8ae5a853-88a7-4577-821c-83c67f23628b
      acceptance_criteria: all 3 criteria confirmed by human-tier manual_approval evidence pinned to b285f21c84e79a12e7605baf4411b251950776b6; PASSED.
    [PASSED        ] documentation        (required)  evidence: ce29be1f-d470-4f59-baf1-ff5f92d44caf
      documentation (no required paths — findings mode): system-tier documentation_update evidence ce29be1f-d470-4f59-baf1-ff5f92d44caf pinned to b285f21c84e79a12e7605baf4411b251950776b6 documents a change; PASSED.
  Note (OP-A): acceptance / scope / human_approval report PENDING here until the interactive operator-confirmation capture lands (OP-3 follow-on) — no operator confirmations exist yet. This is honest and expected, not a bug; the machine checks (tests / lint / documentation) are evaluated against system-tier evidence at this commit.
```

## PM delivery report
# Delivery metrics

_Generated 2026-07-05T18:46:15.303142+00:00 — read-only; computed from stored tickets and DebtItems (no Linear calls, no writes)._

## Throughput (tickets done per week)

| Week | Done |
| --- | --- |
| 2026-W27 | 1 |

## Cycle time per state (historical)

> Historical per-state cycle time over **completed episodes** from the `TicketStatusTransition` log (ATLAS-121/126). An episode is a state entered and later exited; the initial state before the first recorded transition (no recorded entry) and the current open episode after the last (no recorded exit) are **not** counted. A state re-visited N times contributes N episodes.

| State | Episodes | Min (h) | Median (h) | Max (h) |
| --- | --- | --- | --- | --- |
| ready_for_agent | 1 | 2.26 | 2.26 | 2.26 |
| review_required | 1 | 0.02 | 0.02 | 0.02 |

## Ready-queue depth

1 ticket(s) in `ready_for_agent`.

## Anomaly counts

| Type | Count | Recurring tickets |
| --- | --- | --- |
| out_of_ownership_transition | 2 | 0 |
| review_cycle | 0 | 0 |
| dwell_breach | 0 | 0 |
| stale_block | 0 | 0 |

## Dwell breaches

No dwell breaches recorded.

## Tick failures

0 recorded PM-scheduler tick failure(s).

## Follow-ups
(File observed follow-ups as `atlas:proposed-follow-up` comments — the
producer/consumer path is live; eat the dogfood. List them here for the record.)

---
Milestone base case: a ready, context-rich fixture ticket flowed
pack → Symphony → PR → evidence → verification → Done, with human steps
only at the defined gates (apply, confirm, merge). ATLAS-90 formalises
exactly this sequence.
