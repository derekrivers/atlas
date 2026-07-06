---
title: "Document the delivery loop under docs/"
objective: "A file under docs/ states that changes to this repository flow through the Atlas delivery loop."
context: "Smoke B operator runbook, Phase 1 (second fixture; supersedes the retired README fixture). A deliberately small, inert docs change whose point is to exercise the loop, not the work. It lives under docs/ so the PR produces system-tier DOCUMENTATION_UPDATE evidence at the head commit — the verification engine's documentation check (required for ticket_type: documentation, findings mode) counts only docs/ paths, which is exactly why the README-resident predecessor could never verify."
ticket_type: "documentation"
epic_ref: "ATLAS-E1"
acceptance_criteria:
  - "A file under docs/ contains a \"Delivery loop\" heading with exactly one paragraph under it."
  - "The paragraph describes the loop as plan -> pack -> dispatch -> PR -> evidence -> verification -> Done."
  - "The paragraph names the acceptance gate as operator-owned (ADR-0008)."
non_goals:
  - "No file outside docs/ is created or modified; README.md is untouched."
  - "No canonical spec under docs/atlas/ is modified — the note is a new standalone page (plus its docs/MANIFEST.md registration if the doc linter requires one)."
test_requirements:
  - "The doc linter passes on the branch."
definition_of_done:
  - "The Delivery loop page exists under docs/, the linter is green, and CI passes."
---

# Smoke B fixture (v2): document the delivery loop under docs/

Add a single short "Delivery loop" page under `docs/` stating that changes to
this repository flow through the Atlas delivery loop
(plan -> pack -> dispatch -> PR -> evidence -> verification -> Done), naming
the acceptance gate as operator-owned (ADR-0008). One heading, one paragraph;
register the page in `docs/MANIFEST.md` if the doc linter requires it, and
touch nothing outside `docs/`.

Deliberately small and inert: the smoke tests the LOOP, not the work. The
docs/ residency is load-bearing — it is what makes the required documentation
check satisfiable by system-tier evidence at the head commit.
