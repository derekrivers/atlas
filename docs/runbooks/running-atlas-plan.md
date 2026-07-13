# Running `atlas plan` and `atlas apply`

Operator runbook for the planning workflow. `atlas plan` reads the committed
Atlas documents and proposes a backlog diff (a `PlanRun`, never written to
`docs/planning/`); `atlas apply` shows that diff, takes your confirmation, and
atomically writes the renders. This documents what the commands actually do
today — flags, exit codes, and failure messages are as implemented in
`atlas/cli.py`.

## At a glance — and the current limitation

```sh
uv run alembic upgrade head        # once: create the database schema
export ANTHROPIC_API_KEY=sk-ant-…  # programmatic/API billing (see Prerequisites)
git status                         # the input set must be clean & committed
uv run atlas plan                  # ~15 minutes; prints a diff, persists a PlanRun
uv run atlas apply                 # review the diff, confirm y/N, writes docs/planning/
```

**Known limitation, stated up front:** the committed Atlas corpus currently
produces a proposal at ~95–97% of the model's single-call output ceiling. A full
`atlas plan` run takes **~15 minutes** and **may truncate** — recorded honestly as
a failed run with a specific truncation reason (it is *not* a corruption or a bug
in your setup). This is a known boundary; its resolution — staged generation — is
runnable via `atlas plan --staged`, for both a first run and a re-plan of an
existing backlog (see [The capacity boundary](#the-capacity-boundary) and
`docs/atlas/planning-large-corpora.md`). For a single-call run that truncates,
re-running often succeeds, because natural length variation tips the corpus over
the edge.

## Prerequisites

### 1. The `atlas` command

`atlas` is the package's console entry point. After `uv sync`, run it with
`uv run atlas …` (or `python -m atlas …`). All examples below use `uv run atlas`.

### 2. The database schema

Operational state lives in a database (ADR-0006). The default is SQLite at
`.atlas/atlas.db` (gitignored); override with `ATLAS_DATABASE_URL` or the `--db`
flag. Create the schema with Alembic — this is the only supported way to
provision a real database (`Database.create_all` is for tests):

```sh
uv run alembic upgrade head
```

Alembic reads the same URL the CLI does (`ATLAS_DATABASE_URL`, else the default),
so set `ATLAS_DATABASE_URL` once if you are not using the default and every step
below will agree on the database.

### 3. The ATLAS product row

`atlas plan` attributes its `PlanRun` to a product keyed `ATLAS`. That row must
exist before the first plan, or the command exits with a precondition error
(`no 'ATLAS' product in the database…`, exit 2).

There is **no CLI command to create it yet** (an `atlas init` is a known
follow-up). Insert it once with a short Python snippet, against the same database
the CLI uses:

```python
from datetime import UTC, datetime
from uuid import uuid4

from atlas.core.models import Product
from atlas.storage import Database, ProductRepo

now = datetime.now(UTC)
ProductRepo(Database()).add(  # Database() uses ATLAS_DATABASE_URL or .atlas/atlas.db
    Product(
        id=uuid4(),
        key="ATLAS",
        name="Atlas",
        description="Stateful organisational operating system.",
        vision="Repeatable software delivery through a self-improving harness.",
        status="active",
        created_by_type="human",
        created_by_id="operator",
        created_at=now,
        updated_at=now,
    )
)
```

Run it with `uv run python - <<'PY'` … `PY`, or paste it into `uv run python`. Do
this after `alembic upgrade head` (the table must exist) and only once.

For a repeatable fresh start — wipe the local DB and re-seed this product in one
command — use the dev reset script (after `alembic upgrade head`):

```sh
uv run python scripts/reset_db.py --yes
```

It empties every table (schema and migration head preserved) and re-seeds the
`ATLAS` product, leaving the DB plan-ready; pass `--no-seed-product` to wipe
without re-seeding. This is the one-command path that subsumes the snippet above
when you just need a clean backlog (e.g. before a first-run `--staged` run).

### 4. `ANTHROPIC_API_KEY`

`atlas plan` calls the model through the Anthropic API and reads
`ANTHROPIC_API_KEY` from the environment. This is **programmatic (API) billing**,
not a Claude subscription's interactive pool — a subscription login does not
satisfy it. A missing key is a specific precondition error
(`ANTHROPIC_API_KEY is not set…`, exit 2); the key is never logged or written to a
`PlanRun`.

Caveat: Claude Code (or any tool) running in the same shell reads the same
`ANTHROPIC_API_KEY`. Exporting a key here also hands it to anything else in that
environment — scope it deliberately.

### 5. A clean, committed working tree

Ingestion is HEAD-atomic: every input document's content is the blob at its
recorded git SHA (ADR-0006). If any file in the input set (`PRODUCT.md`,
`ARCHITECTURE.md`, `ROADMAP.md`, `WORKFLOW.md`, accepted `docs/decisions/*.md`,
`docs/atlas/*.md`, `docs/domain/*.md`) is **dirty or untracked**, `atlas plan`
refuses with a typed error and plans nothing. **Commit (or stash) first.** This is
the most common first-run surprise — a half-finished doc edit makes planning
refuse until it is committed.

## Running `atlas plan`

```sh
uv run atlas plan
# options: --similarity-threshold F   reconciler threshold (default 0.85)
#          --db URL                    database URL (overrides ATLAS_DATABASE_URL)
#          --repo PATH                 repository root (default: current directory)
#          --staged                    multi-call staged generation (ADR-0010;
#                                       seeds a non-empty backlog to re-plan it)
#          --stubs-only                mint the committed inbox stubs with no
#                                       model call (ATLAS-153); mutually
#                                       exclusive with --staged
```

The command runs the full proposer pipeline: **ingest** the committed documents →
**render** the versioned planner prompt → **call** the model → **parse** the
output → run the **gates** → **reconcile** the proposal against the current
backlog → persist a `PlanRun` at status `proposed`. Expect **~15 minutes**; it
streams the model response.

On success it prints the §2.4 diff — a summary counts line
(`Plan diff: ADD n, MODIFY n, PROPOSE_ARCHIVE n, CONFLICT n`) followed by one
block per entry (type, key or `new:<n>`, title, anchor; MODIFY shows per-field
before/after) — then `PlanRun <id> persisted at status proposed.`

`atlas plan` exit codes:

| Code | Meaning |
| --- | --- |
| `0` | A `PlanRun` was persisted at `proposed`; the diff is printed. |
| `1` | A recorded failure — a `PlanRun` was finalised to `failed`; the machine-readable `failure_reason` is printed (parse, gates, or truncation). |
| `2` | A clean-exit precondition — dirty tree, missing key, missing product, or a model-call error; no `PlanRun` exists. |

`atlas plan` never writes `docs/planning/`.

### Staged generation (`--staged`)

`--staged` generates the proposal across three bounded model calls — epics, then
tickets one call per epic, then dependencies — and the environment assembles the
slices into one complete §3.11 proposal before the gates and reconciler run, which
are unchanged (ADR-0010, `docs/atlas/planning-large-corpora.md`). This is the
designed resolution to [the capacity boundary](#the-capacity-boundary): each call
sits well inside the 64K ceiling. The downstream diff, exit codes, and provenance
are identical to a single-call run; a stage that still truncates is recorded as a
`failed` run whose reason names the stage.

**Re-plan seeding (ATLAS-144).** `--staged` works for both a first run and a
re-plan of a non-empty backlog. On a re-plan the environment renders the current
backlog from the database into each stage — the existing epics into the epics
stage, and each epic's existing tickets into that epic's tickets stage (in
natural-key order) — so the model re-emits the full desired backlog, echoing the
real `ATLAS-<n>` keys of unchanged work. The result is still one full-state §3.11
proposal, so the reconciler diffs it exactly as a single-call re-plan: unchanged
items stay put, and nothing is archived merely because a stage was bounded. The
one shape staged generation cannot yet seed is an **epic-less ticket** (a
`tech_debt` ticket with no epic): stage 2 batches per epic, so `--staged` refuses
a backlog containing one with a clean-exit precondition (exit `2`) rather than
omit — and thereby archive — it. Attach such tickets to an epic, or re-plan them
without `--staged`; an unassigned stage-2 batch is a tracked follow-up.

### Stubs-only mode (`--stubs-only`)

`--stubs-only` mints the committed inbox stubs **without a model call**: the
proposal is built by pure code — the current backlog re-stated verbatim with
its real keys, plus one promoted ADD per committed stub (the same ATLAS-146
promotion the generative path runs) — and flows through the same gates, the
same reconciler (the ATLAS-151 collapse pre-pass runs and is trivially a
no-op: there are no model tickets to collapse), and the same `PlanRun` →
`atlas apply` path, stub retirement included. Deterministic by construction
(ADR-0005): same stubs + same backlog ⇒ the same diff, exactly one ticket ADD
per stub. This is the designed replacement for burning a **~£5, ~15-minute
generative draw** whose only planning contribution was echoing the backlog so
promotion could inject the stubs — and it closes that draw's F-4
double-emission surface, since there is no model output to re-emit a stub.

Because no `PlannerClient` is constructed, **`ANTHROPIC_API_KEY` is not
required** (Prerequisite 4 does not apply); the run completes in seconds.
`--stubs-only` and `--staged` are mutually exclusive — combining them is an
argparse error.

**Empty inbox.** An empty committed inbox is a clean-exit precondition
failure (exit `2`) whose message names the inbox — never an empty-diff
`PlanRun`. Commit stubs first, or run a generative plan.

**Declaring dependencies: the `depends_on` front-matter contract.** A stub
may carry an optional `depends_on` list naming the edges its ticket needs;
promotion turns each entry into a `depends_on` dependency ADD with a single
pinned mechanical reason (never inferred per-stub, ADR-0005). Each entry is
one of:

- an **existing ticket key** (`ATLAS-26`) → an edge from the new ticket to
  that ticket;
- a **sibling stub filename in the same batch** (`inbox-stub-foo.md`,
  basename match) → an edge between the two new tickets;
- anything else: an `.md` name matching no sibling fails closed at plan time
  (a typed stub error, exit `2`); a key that exists nowhere fails gate 3
  (`GATE3_UNRESOLVED_TARGET`) as a recorded `failed` run (exit `1`).

The contract is honoured identically on generative runs, so a stub means the
same thing whichever door mints it.

**Stub authoring under stubs-only.** `epic_ref` must name an **existing epic
key** — there is no model to create a `new_epic:<n>` placeholder, so a
placeholder ref is a typed precondition failure (exit `2`). The default
`source_anchor` — the stub's own first heading at its durable
`inbox/processed/` path (ATLAS-159) — stays resolvable after apply retires
the stub, so it needs no special handling; declare an explicit
`source_anchor` only when a corpus document (`docs/atlas/…`) is the truer
source for the ticket.

**Provenance.** A stubs-only `PlanRun` records `generation_stages: []` (zero
stages — unreachable generatively, so the stored record distinguishes the
modes on its own), `model_provider: none`, `model_name: stubs-only`, and
`prompt_version: stubs-only`; `raw_output_hash` is over the constructed
proposal's canonical JSON. `input_doc_shas` still pins corpus + inbox +
`inbox/processed/` (ATLAS-159), so apply's staleness re-check (AT-5)
behaves identically to a generative run.

## Reviewing the diff and running `atlas apply`

```sh
uv run atlas apply           # prints the diff, then prompts: Apply this plan? [y/N]
uv run atlas apply --yes     # pre-confirm (non-interactive / scripts)
# options: --db URL, --repo PATH
```

`atlas apply` loads the latest `proposed` `PlanRun`, re-checks that the documents
have not changed since it was planned, prints the §2.4 diff, and asks for
confirmation:

- **interactive:** answer `y` to apply, anything else rejects;
- **`--yes`:** pre-confirms, for non-interactive use;
- **neither a TTY nor `--yes`:** apply **refuses** rather than assume consent
  (`apply needs confirmation: re-run with --yes`, exit 2).

On confirmation, apply **atomically** writes the four renders —
`docs/planning/epics.yaml`, `tickets.yaml`, `dependencies.yaml`, and
`roadmap.mmd` — assigns keys from the monotonic counter, persists the backlog, and
finalises the `PlanRun` to `applied`. **Apply is the only legal writer of
`docs/planning/`** (ADR-0006/0007); the doc linter flags any hand-edit there.

`atlas apply` exit codes:

| Code | Meaning |
| --- | --- |
| `0` | Applied; the renders are written and the `PlanRun` is `applied`. |
| `1` | You rejected the diff (`N`); the `PlanRun` is `rejected` and nothing was written. |
| `2` | A refusal/precondition — no proposed plan, a stale plan, a dirty tree, an unsupported (MODIFY) or CONFLICT diff, or no way to confirm. |

## Failure modes — what each means and what to do

Every row is the actual message and exit code from the commands.

| Symptom (printed message) | Command | Exit | What to do |
| --- | --- | --- | --- |
| `input documents are dirty or untracked: […]; planning runs only against committed state` | plan / apply | 2 | Commit or stash the listed files, then re-run. |
| `ANTHROPIC_API_KEY is not set; export it to run \`atlas plan\`…` | plan | 2 | `export ANTHROPIC_API_KEY=…` (Prerequisite 4). |
| `no 'ATLAS' product in the database; bootstrap the product before planning` | plan | 2 | Create the product row (Prerequisite 3). |
| `model call failed: …` / `model call failed after 3 attempts: …` | plan | 2 | Transient (network/timeout/API). A mid-stream transport drop is auto-retried (3 attempts, 1s/2s backoff) before this surfaces — the `after 3 attempts` variant means every retry was exhausted. Re-run. |
| `Plan failed (recorded):` + `{"stage": "truncation", …max_tokens=64000…}` | plan | 1 | The capacity boundary (below). Re-running a single-call plan often succeeds; the durable fix is `atlas plan --staged` (first run or re-plan). |
| `the staged path cannot seed epic-less ticket(s) […]` | plan (`--staged`) | 2 | The backlog has a `tech_debt` ticket with no epic; staged generation batches per epic and cannot seed it. Attach it to an epic, or re-plan without `--staged`. |
| `the committed follow-up inbox 'docs/planning/inbox' has no stubs: nothing to mint…` | plan (`--stubs-only`) | 2 | The inbox is empty. Commit stubs first, or run a generative plan. |
| `inbox stub '…' cannot be promoted: … (field '…')` | plan | 2 | The committed stub's front-matter is missing, invalid, or a `depends_on` entry names no sibling in the batch. Fix the stub and re-commit. |
| `inbox stub '…' declares epic_ref 'new_epic:…'…` | plan (`--stubs-only`) | 2 | Stubs-only has no model to create epics; point `epic_ref` at an existing epic key. |
| `Plan failed (recorded):` + `{"stage": "parse", …}` | plan | 1 | The model output was not valid JSON. Re-run; if persistent, the prompt/model needs attention. |
| `Plan failed (recorded):` + `{"stage": "gates", "failures": […]}` | plan | 1 | A validation gate failed. Read the per-failure `gate`/`code`/`reason` — usually an unresolvable `source_anchor` (gate 4), an orphan epic (gate 5), or an oversized ticket (gate 7). |
| `no proposed PlanRun to apply; run \`atlas plan\` first` | apply | 2 | Run `atlas plan` first (the last run failed or none exists). |
| `the plan is stale: input documents changed since planning; re-run \`atlas plan\`` | apply | 2 | A document changed after the plan. Re-run `atlas plan`, then apply. |
| `inbox stub retirement collision: active stub '…' cannot be retired because '…' already exists` | apply | 2 | The same stub basename exists in both the active inbox and `processed/`. Rename or remove one, then re-plan/apply. |
| `the diff touches frozen ticket(s) (…); apply refuses a diff with CONFLICT entries` | apply | 2 | The proposal would change an in-progress/done ticket (AT-4). Re-plan; frozen tickets are immutable to planning. |
| `the diff contains MODIFY entries; MODIFY application is a follow-up…` | apply | 2 | Applying field changes to existing tickets is not built yet. Only ADD/PROPOSE_ARCHIVE/CONFLICT diffs apply today. |
| `apply needs confirmation: re-run with --yes (no TTY available).` | apply | 2 | You are non-interactive; re-run with `--yes`. |
| `Plan rejected; no renders written.` | apply | 1 | You answered `N`. Nothing was written; re-plan or re-apply when ready. |

## Provenance — what a run recorded

Every `PlanRun` row records, for audit: `input_doc_shas` (the git blob SHA of each
input document — what was read), `prompt_version` and `prompt_hash` (what was
asked), `model_provider`/`model_name`/`model_parameters` (which model and
settings), `raw_output_hash` (what came back), `similarity_threshold`,
`diff_summary` (the counts and entries), `status`, and on a failure
`failure_reason`. On apply it also records `approved_by` and `applied_at`. A
**failed** run keeps the full chain (including `raw_output_hash`) — it is as
auditable as a successful one.

Each written render carries a header comment with the `plan_run_id` (and the
prompt version and key high-water marks), so an applied backlog traces back to the
exact `PlanRun` that produced it. The provenance chain reads
`input_doc_shas → prompt_hash → raw_output_hash → proposal → renders`.

`generation_stages` records how the proposal was generated: one record for a
single-call run, three for a staged run, and `[]` for a stubs-only run — the
empty list is the stubs-only mode marker (ATLAS-153; see
[Stubs-only mode](#stubs-only-mode---stubs-only)).

## The capacity boundary

A proposal is a single model response, and the committed Atlas corpus now produces
one at ~95–97% of `claude-sonnet-4-6`'s 64K output-token ceiling. There is no
higher limit to set. So a full `atlas plan` run sometimes fits and sometimes
truncates from one run to the next — truncation is detected and recorded as a
`failed` run with the `{"stage": "truncation", …}` reason, never misreported as a
parse error or a corrupt write.

The resolution is staged generation: generate the proposal in bounded stages
(epics → tickets → dependencies) and assemble one complete full-state proposal
before reconciliation, so the reconciler and the proposal contract are unchanged.
See `docs/atlas/planning-large-corpora.md` and ADR-0010; the implementation is
tracked as ATLAS-103..107, with re-plan seeding added by ATLAS-144. The staged
path is runnable via [`atlas plan --staged`](#staged-generation---staged) for both
a first run and a re-plan of a non-empty backlog. For a single-call run that
truncates, re-running will usually succeed.

## See also

- `docs/atlas/planning-engine-specification.md` §2.1 (`atlas plan`), §2.2
  (`atlas apply`), §2.4 (diff presentation), and the "Failure contract" /
  "Output capacity boundary" paragraphs.
- `docs/atlas/planning-large-corpora.md` — the staged-generation design (ADR-0010).
- ADR-0006 (source-of-truth hierarchy), ADR-0007 (generative planning with
  deterministic reconciliation), ADR-0010 (multi-call generation with
  single-proposal reconciliation).
