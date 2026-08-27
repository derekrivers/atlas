# Atlas Knowledge & Context Consolidation Design

> **Repository adoption record — 27 August 2026.** The operator-ratified
> Knowledge & Context Consolidation Design v1 is adopted here as the dedicated
> canonical design authority for this programme. Adoption changes repository
> design authority only: implementation requires later governed planning and
> delivery.

**Status:** Ratified canonical design.
**Architecture version:** v1.
**Programme:** Atlas.
**Evidence baseline:** `d69bc9bee004bbb4cae08b3df5dd8bad439dce55`; this is the historical investigation baseline, not a current-state claim.
**Ratified source identity:** SHA-256 `cac1f3848785bc8bf2d4f418a417ecda7bbcceb5c19361d1531b8ef08f9d8f3b`.
**Basis:** H2–H6 read-only investigations in the compiled H1–H6 evidence package, followed by operator ratification.

## 1. Executive decision

Atlas does not primarily have a documentation-volume problem. It has an **authority-selection, projection, measurement, and freshness problem**.

At the audited baseline the committed planner corpus contains 37 files and approximately 858k characters / 214k heuristic tokens. `docs/atlas` contributes about 95% of that corpus. A representative 13-epic staged run repeats stable document and anchor material to roughly 3.73 million heuristic input tokens before schemas, dynamic stage material, or output. Atlas does not currently persist the provider usage, cache usage, retry, or latency evidence needed to measure the real economics.

The response is not to delete historical knowledge or split documents blindly. Atlas will:

1. measure planner requests before optimising;
2. make document roles explicit rather than path-implicit;
3. separate planner selection from anchor/context compatibility resolution;
4. distinguish live operational projection from planner authority projection;
5. narrow planning context stage-by-stage using deterministic selection and paired evaluation;
6. preserve research/history without keeping it in every planner payload;
7. add only targeted semantic-freshness metadata where high-risk mixed prose requires it;
8. build a minimal deterministic Authority Context Graph only as a read-only discovery projection;
9. defer graph databases and semantic/vector retrieval until measured evidence demonstrates a need.

> **Programme principle:** Knowledge may grow without bound. Active context may not. Authority must remain singular, current, deterministic, and mechanically discoverable.

---

## 2. Goals

The programme must:

- reduce repeated planner context without weakening planning completeness;
- make authority selection deterministic and reconstructable;
- preserve every research source, experiment result, negative finding, superseded decision, and historical implementation record;
- prevent live facts from being manually duplicated across long-lived design prose;
- preserve legacy anchors and `relevant_docs` compatibility during restructuring;
- detect high-risk semantic staleness from the owning authority rather than modification time;
- make model economics measurable;
- preserve fail-closed behaviour whenever authority, coverage, identity, or freshness is ambiguous.

A new governance mechanism earns permanence only when it produces measurable safety/correctness value, measurable cost/latency value, or removes an older manual burden.

---

## 3. Non-goals

This programme does **not**:

- create a second source of truth;
- make generated material authoritative by generation alone;
- replace repository documents as intent authority;
- replace the Atlas store as operational-state authority;
- replace GitHub CI/system-tier evidence;
- collapse workflow, delivery policy, runtime, and occupancy into one synthetic “ceiling” fact;
- use LLM or embedding output as authority;
- annotate every sentence;
- create a claim database;
- introduce Neo4j in v1;
- introduce vector/semantic retrieval in v1;
- move or delete the existing cumulative programme document during initial migration;
- retire the Phase 13–20 horizon while Phase 15 remains open;
- weaken exact-head, source-anchor, validation, or Context Pack fail-closed semantics;
- change ATLAS-253 execution authority.

---

## 4. Authority model

Existing Atlas authority remains binding:

- **Canonical repository documents** own intent, design, normative constraints, and governed procedure.
- **Atlas operational store** owns operational state.
- **Linear, GitHub, Symphony and runtime systems** own the external/live facts already assigned to them.
- **Planning renders** remain review/persistence renders, not operational authority.
- **Generated projections** expose or compare owning sources; they do not own projected values.

Every projection must preserve the identity of each source it projects.

A projection may collect, normalise, compare and fingerprint. It may not reconcile competing owners, infer missing authority, write back, silently reuse stale values, or promote advisory values into authority.

## 4.1 Relationship to current Atlas authority

This document owns the detailed architecture, decisions, non-goals, rejected
approaches, invariants and delivery ordering of Knowledge & Context
Consolidation. It does not displace the master plan or the source-of-truth
hierarchy:

- `docs/atlas/atlas-master-plan.md` remains Atlas's single strategic master
  plan.
- `docs/atlas/agentic-engineering-programme-design.md` remains at its current
  path as the cumulative programme, research and compatibility/preservation
  baseline. Its body and history are not migrated by this adoption. Where that
  document points here, this document owns the detailed Knowledge & Context
  Consolidation architecture.
- `docs/atlas/phase-13-20-programme-horizon.md` retains its existing authority
  while Phase 15 is open. This programme does not block the ATLAS-253 live ramp
  unless a separately ratified dependency emerges.
- `docs/atlas/phase-16-agent-runtime-and-integration-safety.md` continues to own
  detailed Phase 16 runtime and integration-safety design. This programme
  neither changes Phase 16 activation authority nor turns its own discovery
  projections into runtime authority.
- `docs/atlas/planning-engine-specification.md` continues to own today's
  planning input, plan/apply, reconciliation, gate and PlanRun contracts until
  later accepted changes implement this design.
- `docs/atlas/context-renderer.md` continues to own today's ticket-context
  retrieval, budgeting, provenance and validation contracts until Wave 2
  separately changes resolution participation under the future registry.

At adoption, current planner ingestion includes this document because it
selects the broad `docs/atlas/*.md` corpus. That temporary inclusion is a
consequence of today's compatibility behaviour, not evidence that the future
Document Role Registry or planner profiles already exist.

The first implementation work begins, after governed planning, with planner
model-call telemetry and then the Document Role Registry in compatibility mode.
No capability, ticket, planning input or operational state is created by this
design adoption.

---

# Part I — Planner observability

## 5. Planner call telemetry

Planner telemetry is the first implementation capability.

For each physical provider request Atlas should be capable of retaining bounded evidence including:

- PlanRun identity;
- stage;
- logical attempt and physical transport attempt;
- provider/model identity;
- prompt/template version;
- exact input identity;
- prompt byte/character count;
- prompt-segment sizes;
- provider-reported input/output tokens;
- cache-creation/cache-read tokens where available;
- reasoning/thinking tokens where exposed;
- stop reason;
- wall latency;
- time to first token where measurable;
- retry category;
- output size;
- parse/schema/gate outcome.

Provider-specific optional fields remain unsupported/null rather than fabricated. Prompt contents do not need to be persisted merely to record economics. Raw provider payloads and secrets are excluded.

Atlas must be able to answer how many physical calls a PlanRun made, cost-bearing token totals by stage, whether caching actually hit, and whether narrower context reduced cost without degrading planning outcome.

---

# Part II — Document Role Registry

## 6. Problem

Current location-based planner ingestion conflates several different questions:

- Is this document canonical?
- Should this planning stage receive it?
- May new work anchor to it?
- Must legacy anchors still resolve against it?
- May Context Renderer retrieve it?
- Is it preservation-only?

The current `docs/atlas/*.md` selection also matches nested paths. Splitting one large document into active and preservation files beneath `docs/atlas/` would therefore not, by itself, reduce planner context.

## 7. Decision

Introduce a committed **Document Role Registry**.

At minimum, the registry must be capable of expressing semantics equivalent to:

- `authority_class`
- `planner_profiles[]`
- `new_anchor_allowed`
- `anchor_resolution`
- `context_resolution`
- `historical_only`
- `owner`

Exact serialization is an implementation detail. The semantic separation is binding.

Directory location must no longer be the sole policy signal once migration begins.

## 8. Compatibility mode first

The first registry release must be behaviour-preserving:

1. reproduce today's planning corpus exactly;
2. preserve deterministic ordering;
3. preserve existing valid anchor choices;
4. preserve Context Renderer resolution;
5. record registry identity in PlanRun provenance;
6. fail closed on uncatalogued governed planning documents.

No document restructuring occurs before this compatibility checkpoint passes.

---

# Part III — Planner selection versus compatibility resolution

## 9. Required logical sets

Atlas must distinguish:

1. **planner payload corpus**
2. **new-anchor choice corpus**
3. **legacy anchor-resolution corpus**
4. **Context Renderer resolution corpus**
5. **preservation-only corpus**

These sets may overlap but are not identical.

After migration Atlas must be able to omit preservation bodies from prompts while continuing to resolve existing stored anchors and `relevant_docs`.

Migration must fail closed on unresolved existing anchors, profile ambiguity, missing compatibility material, uncatalogued documents, and silently lost stored references.

Named planner profiles should eventually control the planner payload. Unknown or ambiguous profile selection fails before a model call.

---

# Part IV — Two distinct projections

## 10. Operational Current-State Projection

Atlas should expose a read-only, non-committed operational projection, conceptually:

```text
uv run atlas current-state --json
```

It projects bounded facts from existing owners and owns no value itself.

It must:

- be schema/versioned;
- record one observation time;
- preserve per-source identities;
- emit source failures;
- never silently fall back to an older successful value;
- keep committed workflow identity distinct from loaded runtime identity;
- keep delivery policy distinct from runtime state;
- report coherence comparisons without reconciling them;
- perform no writes or runtime/service actions.

Candidate v1 field families include:

- projection schema/status/time/fingerprint;
- Atlas `main` identity;
- structured programme position;
- active programme revision;
- committed workflow identity and bounded configuration;
- active delivery-policy revision and values;
- latest coherent PM/store observation;
- bounded non-terminal ticket summary;
- managed Symphony runtime identity;
- coherence results;
- bounded source-error codes.

Do not initially include an ambiguous single `proven_ceiling`, `ramp_gate`, `active_planning_batch`, global external-pin set, inferred occupancy, or a collapsed committed/runtime `max_concurrent_agents`.

Live projection output must not be committed to Git.

## 11. Planner Authority Projection

The Planner Authority Projection is different.

It is the exact deterministic repository-authority package selected for one planning stage. It should retain:

- planner profile identity;
- Document Role Registry version/digest;
- selected document paths and blob SHAs;
- selected section/anchor identities;
- compact projections replacing full documents;
- projection fingerprint.

The PlanRun must retain enough provenance to reconstruct exactly what authority the model received.

The Planner Authority Projection does not automatically ingest live store/runtime state.

---

# Part V — Stage-specific context

## 12. Stage 2 first

Stage 2 is the first context-narrowing target because it repeats the largest stable authority payload once per emitted epic.

The initial selector must be opt-in and no-apply.

For one representative epic, compare:

```text
full current Stage-2 authority
vs
deterministically selected Stage-2 authority
```

against the same repository HEAD, planning input, backlog snapshot, model/prompt version, and deterministic settings.

A narrowed Stage-2 package must preserve at least:

- target epic;
- current tickets under it and frozen identities;
- exact target source sections and parents;
- accepted ADR consequences;
- relevant interface/implementation/runbook constraints;
- active/promoted lessons where already authorised;
- relevant active stub contract;
- compact sibling/cross-epic context required for correctness.

No default rollout until paired evaluations show unchanged schema/gate correctness, existing-ticket retention, required source coverage, no inappropriate archive/conflict outcomes, and acceptable stability across representative epic sizes.

## 13. Stage 3 second

Before narrowing Stage 3, enrich the dependency projection with enough bounded context to preserve:

- ticket objective;
- component;
- source anchor;
- current dependency edges;
- interface ownership/consumer relationships where available;
- protected shared surfaces;
- ordering constraints and ambiguity evidence.

Omission must not silently cause existing dependencies to be archived.

## 14. Stage 1 last

Stage 1 narrowing is last because it saves fewer repeated corpus copies and has the highest omission risk: missing strategic intent can prevent an epic from being proposed at all.

---

# Part VI — Provider caching

## 15. Stable-prefix experiment

Caching is an experiment, not an assumption.

Do not optimise for provider caching until telemetry measures cache creation/read usage.

The candidate architecture is stable authority rendered in early provider-compatible content blocks, followed by stage/epic-specific dynamic suffixes.

Identical prompt hashes are not proof of a provider cache hit.

---

# Part VII — Active programme and preservation

## 16. Existing cumulative programme document

The existing cumulative programme document remains at its current path as a compatibility and preservation baseline during v1 migration.

It must not be destructively rewritten and removed.

This preserves:

- existing `relevant_docs` paths;
- anchor/path identity;
- prose/test/validator references;
- historical research and decisions;
- a simple rollback path.

After atomic adoption it may become canonical historical/preservation authority rather than active-current programme authority.

## 17. Target document architecture

The eventual architecture separates:

### Active programme authority

A concise canonical document carrying current binding programme rules, active phase/order/gates, authority boundaries, and current cross-cutting decisions.

### Future horizon

A scoped provisional horizon selected only by explicit planning profiles.

### Dedicated active phase designs

Detailed active phase authority remains separate and is selected only when relevant.

### Preservation corpus

Canonical historical material may include:

- the cumulative v4 compatibility baseline;
- research/experiment ledger;
- decision/supersession ledger;
- migration map.

Preservation material remains authoritative as history without entering every planner payload.

## 18. Preservation invariants

No migration passes unless it proves preservation of:

- every external research URL;
- Atlas-specific interpretation and source limitation;
- experiment method, exact identity, verdict, negative findings, and limitations;
- decision identities and original wording;
- supersession history;
- threat identities;
- metric definitions/evidence;
- anti-patterns;
- future programme direction;
- ticket DoD principles;
- historical operating snapshots;
- failed attempts and incidents.

No original heading may disappear without an explicit disposition.

The Phase 13–20 horizon remains active until the already-governed Phase 15 closure/supersession transition.

---

# Part VIII — Claim roles and semantic freshness

## 19. Claim model

The v1 claim roles are:

- `NORMATIVE`
- `CURRENT_SNAPSHOT`
- `HISTORICAL_EVIDENCE`
- `GENERATED_PROJECTION`

Lifecycle is separate:

- `ACTIVE`
- `SUPERSEDED`

`SUPERSEDED` is not a fifth role.

Generated content becomes normative only through governed review/adoption.

## 20. Granularity

- homogeneous artifact families should use path/file classification where possible;
- section-level metadata is the normal explicit mechanism;
- claim-level metadata is exceptional.

Do not annotate every paragraph. Prefer removing duplicated current prose or pointing to the owner.

## 21. Freshness

Freshness is based on the semantic value owned by a declared dependency, not modification time.

A resolver returns bounded evidence equivalent to:

- authoritative source identity;
- canonical semantic value;
- value fingerprint;
- owning observation time where applicable.

Outcomes:

- source identity moved, relevant value unchanged → `FRESH`;
- relevant canonical value changed → `STALE`;
- source missing/ambiguous/incomplete → `UNKNOWN`;
- malformed declaration → `INVALID`.

Ordinary documentation CI remains network-independent. Repository-resolvable stale claims may fail CI. External/live `UNKNOWN` may warn offline but must fail any live gate relying on that claim.

Explicitly rejected:

- mtime freshness;
- TTL authority;
- LLM freshness verdicts;
- central claim database;
- universal observation/inference/proposed/verified/ratified state machine;
- automatic prose rewriting;
- free-form authority join keys;
- a single whole-document hash as semantic freshness for mixed prose.

---

# Part IX — Authority Context Graph

## 22. Decision

Build a minimal read-only Authority Context Graph spike.

It is a discovery projection, not a new source of truth.

Use an on-demand in-memory `NetworkX MultiDiGraph`. No persistence or graph database in v1.

Candidate node families:

- Product
- Epic
- Ticket
- ADR
- DocumentSection
- Lesson (advisory)
- InterfaceContract only when delivered
- Surface
- Control
- Procedure
- optional ExternalPin

No canonical Phase node until Atlas owns a stable Phase identity.

Candidate narrow edge families include:

- `OWNS`
- `GROUPS`
- `DEPENDS_ON`
- `IMPLEMENTS`
- `RELATES_TO`
- `SUPERSEDES`
- `ANCHORED_TO`
- `REFERENCES`
- `DERIVED_FROM`
- `USES_INTERFACE`
- `BINDS_SURFACE`
- `PROTECTS`
- `SELECTS`
- `ROUTES_TO`
- `PINS`

Avoid vague generic edges such as `APPLIES_TO`.

Every edge must expose authority class, owning source, exact source identity, source revision/fingerprint, derivation identity where derived, and a bounded status.

## 23. Coverage rule

Missing machinery is explicit:

```text
coverage: unsupported
reason: <bounded reason>
```

It must never be represented as an empty list that could be interpreted as “no relationship exists.”

The graph cannot prove absence of unknown semantic coupling, independence from file disjointness, external runtime truth without runtime evidence, or completeness beyond its named enumerators and bounds.

Existing dependency, protected-lane, interface-certification, validation-plan, and operator fail-closed mechanisms remain authoritative.

## 24. Evaluation gate

Before Context Renderer integration, the spike must prove:

- byte-identical output/fingerprint for identical snapshots;
- exact provenance on authoritative edges;
- equivalence with existing dependency/anchor/protected-lane/validation mechanisms;
- zero authoritative semantic/vector inference;
- visible incomplete coverage rather than silent truncation;
- existing-control cases route to the stronger existing control;
- no change to current admission/readiness/validation behaviour.

---

# Part X — Deferred technologies

## 25. Graph persistence / Neo4j

Persist graph state only if measurement later shows historical-snapshot, rebuild-cost, incremental-update, or durable interface-history value.

A dedicated graph database is deferred until measured scale/traversal/concurrency/latency requirements show NetworkX plus relational storage is inadequate.

No current evidence meets that threshold.

## 26. Semantic/vector retrieval

Semantic/vector retrieval remains deferred until deterministic enumeration and explicit interface contracts exist and a labelled evaluation set demonstrates materially missed context.

Any future semantic/vector result remains advisory until deterministically or explicitly ratified.

---

# Part XI — Ratified delivery sequence

## 27. Wave 1 — Observe and classify

1. Planner model-call telemetry contract.
2. Provider usage/latency/retry capture.
3. Per-call telemetry persistence/reporting.
4. Document Role Registry schema/linter in compatibility mode.

## 28. Wave 2 — Separate authority participation

5. Planner profile selection/provenance.
6. Separate planner payload from new-anchor choices.
7. Separate legacy anchor and Context Renderer resolution.
8. Migration/integrity reporting for stored anchors and `relevant_docs`.

## 29. Wave 3 — Current state and bounded selection

9. Structured programme-position contract.
10. Operational Current-State Projection v1.
11. Stage-2 deterministic authority selector.
12. Paired full-vs-narrow Stage-2 evaluation harness.

## 30. Wave 4 — Economics and programme restructuring

13. Stable-prefix cache experiment.
14. Active programme authority extraction.
15. Preservation/research/decision integrity tooling.
16. Atomic programme selection/adoption change.

## 31. Wave 5 — Freshness and discovery

17. Targeted claim-role/freshness contract and structural checks.
18. Initial deterministic repository freshness resolvers.
19. Live policy/runtime resolvers for explicit gates.
20. Authority Context Graph spike and retrospective evaluation.

## 32. Wave 6 — Further context optimisation

21. Stage-3 enriched projection and selector.
22. Stage-3 paired evaluation.
23. Stage-1 strategic selector and evaluation.
24. Revisit Stage-2 batching only with measured token/output headroom.

These are candidate implementation boundaries, not minted Atlas ticket keys.

---

# Part XII — Rollout and evidence gates

## 33. General adoption rule

Every new mechanism must demonstrate at least one of:

- measurable correctness/safety improvement;
- measurable cost/latency improvement;
- measurable reduction in reviewer/manual burden;
- replacement of an older mechanism.

Mechanisms that only add metadata/process without measurable value should be narrowed or removed.

## 34. Required adversarial tests

Each relevant slice should include seeded cases such as:

- unknown/uncatalogued document;
- stale registry/profile;
- broken anchor;
- unresolved legacy `relevant_docs`;
- stale current-state source;
- unavailable live source;
- semantic-equivalent source movement;
- genuine semantic value movement;
- unsupported graph family;
- graph bound overflow;
- cache miss despite stable inputs;
- narrowed planner context omitting required authority.

---

# Part XIII — Security and privacy

## 35. Secret exclusion

Telemetry and projections must not retain credentials, tokens/cookies, unnecessary raw provider payloads, raw environment values, raw process command lines, prompt contents merely for economics, or secret-derived hashes that create recovery risk.

Bounded UUIDs, SHAs, content hashes of non-secret canonical data, versions, statuses, counts, and fingerprints remain permitted where already governed.

---

# Part XIV — Explicitly rejected architecture

The programme rejects:

- committed live current-state snapshots;
- programme split before selection/resolution infrastructure;
- deleting historical knowledge to reduce context;
- claim metadata on every sentence;
- central claim database;
- universal confidence state machine;
- mtime/TTL freshness;
- LLM authority/freshness decisions;
- automatic prose supersession;
- one combined workflow/policy/runtime/occupancy ceiling;
- independence inferred from absence of graph edges;
- Neo4j now;
- semantic/vector retrieval as authority;
- Stage-1 narrowing before Stage-2/3 evidence;
- further 00xM sentence-by-sentence stale-prose repairs where the defect belongs to this programme.

---

# Part XV — Remaining bounded implementation decisions

The architecture is ratified. The following are implementation-level decisions during decomposition:

1. exact Document Role Registry serialization/location;
2. initial planner profile set;
3. structured programme-position representation;
4. exact Current-State v1 field subset/publicity;
5. whether an ignored local current-state cache is useful;
6. exceptional section metadata syntax;
7. initial semantic-freshness resolver set;
8. Authority Context Graph bounds/schema;
9. representative Stage-2 evaluation epics and equivalence metrics;
10. cache-block structure after telemetry exists.

These decisions must not reopen the architecture absent contradictory evidence.

---

# Part XVI — Definition of programme success

Knowledge & Context Consolidation succeeds when Atlas demonstrates that:

- planning context is smaller where appropriate but authority-complete;
- token/cache/latency economics are measured rather than estimated;
- every planning run can reconstruct the exact authority supplied to the model;
- preservation/history remains resolvable without entering routine planner payloads;
- volatile operational facts are projected from their owners rather than duplicated manually;
- high-risk current prose can be deterministically marked stale/unknown;
- existing tickets, anchors and Context Packs remain resolvable through migration;
- the Authority Context Graph improves deterministic discovery without displacing existing authority;
- no graph DB or semantic retrieval is adopted without measured need;
- governance burden either decreases or demonstrates measurable safety value.

---

## Appendix A — Research basis

This design incorporates the ratified H2–H6 findings:

- **H2:** planner corpus economics, telemetry gap, Stage-2 repetition and measurement-first sequencing;
- **H3:** non-committed current-state projection with independent source identities;
- **H4:** explicit document roles, planner-selection/resolution separation, and preservation-first migration;
- **H5:** small claim-role vocabulary and source-dependent semantic freshness;
- **H6:** minimal deterministic in-memory Authority Context Graph with explicit coverage gaps.

The investigation package remains evidence. Once reviewed and adopted into the repository's canonical hierarchy, this document is intended to become the active design authority for the Knowledge & Context Consolidation programme.
