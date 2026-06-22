# ATLAS-112 — AT-7 measures anchoring-convention agreement, not work coverage

> Proposed key: **ATLAS-112** (operator to confirm — continues the out-of-band
> 101–111 sequence; sibling of ATLAS-107). In the real loop this enters the
> backlog through plan/apply with key=null until apply assigns it; this record
> is the intent that the next `atlas plan` would read.

**Type:** feature (acceptance-metric refinement)
**Risk:** medium
**Epic:** Generative Planning with Deterministic Reconciliation (Phase 2 / 2.5
follow-on)
**Status:** proposed
**Source anchor:** docs/atlas/planning-engine-specification.md#7-acceptance-tests-milestone-1

---

## The finding (what two live runs revealed)

AT-7 (`anchor_coverage`) checks each hand-written roadmap ticket's
`source_anchor` against the set of anchors the planner emitted, and scores the
fraction matched. Two live staged runs against the real corpus this session
produced:

- **Run A: 82.6%** (10 epics, 88 tickets)
- **Run B: 63.0%** (10 epics, 95 tickets)

A **~20-point swing** between two runs of the same engine on the same corpus.
That swing is the headline: the AT-7 figure is noisy enough that a single run's
number is one sample, not a stable measurement of planner quality. Any process
that "optimises toward 90%" would be chasing this variance (Goodhart's law).

Offline miss-analysis of Run B (free, from the saved proposal) showed **all 34
misses are Phase 4–8 tickets** (Delivery Coordination, Execution Context,
Evidence-Driven Delivery, Autonomous Delivery), and in every case **the work is
present in the proposal but the planner anchored the ticket to its design
document, not to the roadmap epic heading the hand-written ticket used**:

| Roadmap ticket wants | Planner anchored its (present) ticket to |
| --- | --- |
| `implementation-roadmap.md#epic-delivery-coordination` | `pm-engine-and-linear-sync.md#sync-loop`, `#field-ownership`, … |
| `implementation-roadmap.md#epic-execution-context` | `context-renderer.md#retrieval-rules-v1`, … |
| `implementation-roadmap.md#epic-evidence-driven-delivery` | `evidence-pipeline.md#poller`, `#status-normalisation`, … |
| `implementation-roadmap.md#epic-autonomous-delivery` | `symphony-integration.md#…` |

The planner anchored Phase 0–3 (and 7, and 9) tickets to the **roadmap** (so they
matched), but anchored the design-doc-backed phases to the **design docs** (so
they missed). Anchoring a PM-engine ticket to `pm-engine-and-linear-sync.md#sync-loop`
is **arguably more precise** than anchoring it to a vague epic heading — but it
is a *different* convention from the roadmap author's, and exact-string matching
counts it as a total miss.

**Conclusion:** AT-7's exact-anchor metric conflates two different questions —
"did the planner cover the work the roadmap describes?" and "did the planner
anchor it where the roadmap author did?" Work coverage is high (every roadmap
phase has corresponding tickets in the proposal). Anchor-convention agreement is
what bounces between 63% and 83%, driven by an inconsistent planner choice for
~34 design-doc-backed tickets.

---

## Why this is a metric ticket, not a planner ticket

The naive "fix" — instruct the planner to anchor every ticket to the roadmap
epic so the number rises — is rejected. It would **degrade anchor precision**
(the design-doc section is the better source for a design-doc-backed ticket) to
inflate a metric. That is the textbook Goodhart failure and is an explicit
non-goal here.

The honest question is whether AT-7 should measure **work coverage** (does each
roadmap ticket's work appear in *some* planner ticket, regardless of which
document it is anchored to?) rather than **anchor-convention agreement**. Work
coverage is what the milestone actually cares about. This ticket defines and
evaluates that variant and lets the operator decide, at a gate, which metric
becomes the AT-7 acceptance bar — chosen because it measures the right thing
better, never because it produces a higher number.

---

## Prerequisite: fix the miss-analysis tool (it currently overclaims)

`scripts/at7_miss_analysis.py` reported "0 genuine gaps / 100% optimistic
ceiling" for Run B. That classification is **not trustworthy** and must be fixed
before it is used to evaluate anything. The flaw: its "adjacent-anchor" test asks
"did the planner anchor anything into the same *document*?" For
`implementation-roadmap.md` the answer is always yes, because all ten epics
anchor there — so every miss trivially passes the "same doc" test on the strength
of the *epic* anchors, regardless of where the actual ticket landed. The
proximity heuristic is defeated by the roadmap's structure.

Fix: classify a miss as "adjacent" only when the planner anchored a **ticket**
(not an epic) to a *heading near the wanted heading within the same document* —
i.e. compare against headings adjacent in the doc's heading index, not "anything
in the doc." This is a free dev-tooling fix (no API call); it is the first step
because the metric evaluation below depends on a tool that tells the truth.

---

## Scope

In scope:

1. **Fix `scripts/at7_miss_analysis.py`** classification as above so its
   adjacent-vs-genuine split is trustworthy (free, offline).
2. **Define a content-coverage AT-7 variant**: for each hand-written roadmap
   ticket, determine whether *some* planner ticket covers its work by
   title/objective similarity (reuse the reconciler's Sørensen–Dice over
   casefold token sets, the existing similarity primitive — single source), with
   a recorded threshold, independent of `source_anchor` document. Output the same
   shape as `anchor_coverage` (a fraction plus the per-ticket covered/missed
   breakdown).
3. **Evaluate both metrics** against the saved proposals from this session
   (Run A and Run B) and against at least one fresh capture, reporting
   exact-anchor vs content-coverage side by side, and the run-to-run variance of
   each. The hypothesis to confirm or falsify: content-coverage is both *higher*
   and *substantially less variant* than exact-anchor, because it is not hostage
   to the anchoring-convention coin-flip.
4. **Gate decision (operator):** on the evidence, decide whether AT-7's
   acceptance bar becomes content-coverage, stays exact-anchor, or becomes a
   reported pair (exact-anchor as a strict floor, content-coverage as the bar).
   Record the decision and rationale in the planning-engine specification §7 and,
   if the bar changes, in ADR form.

Out of scope (explicit non-goals):

- Changing the planner's anchoring behaviour to push the number up. The planner
  anchoring design-doc-backed tickets to design docs is defensible and is **not**
  to be "corrected" for metric reasons.
- Any change to the §3.11 proposal contract, the gates, or the reconciler.
- Auto-tuning the similarity threshold to maximise the score (it is set for
  correctness and recorded, not optimised against the metric).
- A "loop until 90%" or any unsupervised metric-chasing process.

---

## Acceptance criteria

1. `scripts/at7_miss_analysis.py` classifies a miss as adjacent only on
   genuine heading proximity within a document, not "any anchor in the doc"; a
   unit test seeds the roadmap-clustering case and asserts it is no longer
   classified as a false 100% ceiling.
2. A content-coverage function exists that matches each roadmap ticket to a
   planner ticket by recorded-threshold title/objective similarity, document-
   independent, reusing the reconciler's similarity primitive (no second
   implementation).
3. A free, offline evaluation reports exact-anchor vs content-coverage for the
   two saved proposals and one fresh capture, including each metric's run-to-run
   spread.
4. The evaluation explicitly attributes the exact-anchor variance to the Phase
   4–8 design-doc-anchoring pattern (falsifiable: if the misses are *not*
   predominantly design-doc-anchored Phase 4–8 tickets on a fresh run, the
   finding is wrong and the ticket says so).
5. A recorded operator gate decision on the AT-7 bar, written into the
   planning-engine specification §7 (and ADR if the bar changes), chosen on the
   evidence — not to raise the number.

## Non-goals (restated for the executing agent)

An agent picking this up must not "improve coverage" by changing planner
prompts, anchoring rules, or the §3.11 contract. The deliverable is a *better
measurement and a recorded decision*, not a higher figure.

---

## Relationship to existing follow-ons

- **ATLAS-123** (pair-metric encoding) is where the chosen AT-7 metric is
  wired into the acceptance suite: the exact-anchor floor live, content_coverage
  reported until pinned. This ticket (112) decides *which* metric 123 encodes.
  Sequence: 112 (define + decide) → 123 (encode in the suite) → 124 (pin the
  content bar after a second capture).
- **ATLAS-107** (staged acceptance suite) *reuses* the metric ATLAS-123
  encodes for the staged path; it does not re-derive the bar.
- **ATLAS-106** (batch sizing) is unrelated and remains independently owned.

## Evidence on file (this session)

- Run A proposal scored 82.6% exact-anchor; Run B proposal scored 63.0%.
- Run B miss-analysis: 34/34 misses are Phase 4–8 tickets, all anchored to
  design docs rather than the roadmap epic (raw output retained).
- The saved proposal(s) under `/tmp/full_staged_proposal.json` (copy somewhere
  durable — `/tmp` clears on reboot) are the free, offline inputs for the
  evaluation; no further API spend is required to do steps 1–3.

---

*Recorded 2026-06-17. This ticket exists so the AT-7 finding — that exact-anchor
coverage measures anchoring-convention agreement, not work coverage, and varies
~20 points run-to-run because of it — is tracked with an owner and a falsifiable
scope rather than lost to a chat log. The work it scopes is a more honest
measurement and a gated decision, deliberately not a campaign to raise a number.*
