# Atlas Review Doctrine

How gate presentations and completion reports are reviewed. The
reviewer may be the operator, an AI assistant, or a configured
subagent; this document is the contract any of them applies. The
reviewer recommends; only the operator approves (ADR-0009). A
reviewer's own claims are agent-tier evidence: verify premises
against the repository before relying on them, and mark what was not
verified.

## 1. Gate review (the plan)

- Understanding: does the stated objective match the ticket text and
  the phase milestone it serves?
- Scope: in/out lists complete; every adjacent ticket named; the plan
  touches nothing the prompt excludes; the NOT-doing list is present
  and specific.
- Operator rulings: implemented, not relitigated — but their premises
  VERIFIED against the repository first. A ruling resting on a wrong
  premise is flagged at the gate with a proposed faithful correction,
  never silently obeyed and never silently fixed (precedent:
  ATLAS-23's registry-equality correction).
- Named gaps: each resolved by explicit proposal stating the chosen
  convention, its failure modes, and the same-change canonical-doc
  wording. Explicit declaration over inference. A gap the prompt did
  not name is a stop, not a licence.
- Tests: falsifiable form throughout — contract tests transcribed
  from the documents, never from the code under test; at least one
  negative per behaviour; boundaries asserted on both sides; one
  violation fails in exactly one attributable place.
- Forced consequences: scope the plan's landing forces (a new schema
  construct, a fixture the extension requires) are surfaced AT the
  gate for explicit authorisation, or conditionally pre-authorised
  with both forks stated (precedents: the minimum/maximum learning;
  ATLAS-23's conditional).
- Dependencies, migrations, and manifest diffs declared at the gate,
  never discovered in the diff.
- Doc divergence: any behaviour change names the owning canonical doc
  updated in the same change. Deletion over annotation; pointer over
  copy.

## 2. Close review (the completion report)

- Every definition-of-done criterion maps to named evidence — test
  names, command output, greps with counts. An unmapped criterion is
  not done.
- Evidence typing: the report is agent-tier (ADR-0008). The standard
  verdict is approve-pending-CI; final close requires the system-tier
  CI record pinned to the head commit.
- Judgment calls: each audited against the binding-vs-indicative
  rule. A deviation from a binding element reopens the session; an
  improvement to an indicative element is accepted — and if the same
  call recurs across sessions, it is a missing sentence: name the
  document that should absorb it.
- Diff scope: equals the approved plan plus stated-reason items,
  nothing else.
- Follow-ups: at close, every proposed follow-up gets an owner and a
  deadline, or is dropped with a one-line rationale. Nothing vague
  survives a close.
- The harness question, asked every close: did this session reveal a
  missing rule, and which document absorbs it?

## 3. Verdict forms

Approve / approve with additions (additions as paste-ready text the
operator relays verbatim) / approve pending CI / reopen (binding
deviation or unmapped criterion) / stop (premise mismatch the gate
cannot resolve). Additions become operator-ratified when relayed.

## 4. Reviewer conduct

Quote what was checked; never assert repository state from memory
when it can be read. Calibrate recognition to evidence — name what is
genuinely strong and why, flag what is weak even in an approvable
plan. Review the work, not the worker: a flagged error in an operator
ruling or a reviewer premise is the discipline working, and is
treated as such (precedents: the enums.py path correction; the
registry-equality correction).
