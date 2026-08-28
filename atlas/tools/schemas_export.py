"""Schema export (ATLAS-16): write JSON Schemas for every canonical model.

Writes ``model_json_schema()`` output for each model in
``CANONICAL_MODELS`` to ``docs/generated/schemas/<ModelName>.json``,
deterministically: sorted keys, two-space indent, UTF-8, LF, trailing
newline — regenerating on an unchanged codebase is byte-identical.

``docs/generated/`` is machine-written; this command is its only legal
writer (same rule as ``docs/planning/``, enforced by doc linter v2's
regeneration check). Until the ``atlas`` CLI lands (Phase 2), the entry
point is ``python -m atlas.tools.schemas_export``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from atlas.core.models import (
    AcceptanceSession,
    AdmissionRun,
    AgentRun,
    ArchitectureDecisionRecord,
    Atlas280BootstrapRecoveryReceipt,
    CIHandoffReconciliation,
    ContextPack,
    DebtItem,
    DeliveryAdmissionPolicyRevision,
    Epic,
    Evidence,
    Lesson,
    OperatorActionReceipt,
    PlannedCIPendingRecovery,
    PlannerLogicalCall,
    PlannerPhysicalTransportAttempt,
    PlanningExecution,
    PlanningExecutionOutcome,
    PlanRun,
    PmSyncReceipt,
    Product,
    Ticket,
    TicketDependency,
    TicketStatusTransition,
    TickFailure,
    VerificationCheck,
)
from atlas.planning.proposal import (
    Proposal,
    ProposalDependency,
    ProposalEpic,
    ProposalTicket,
)

# The canonical model sets whose schemas are exported, in name order.
# Completeness tests pin CORE_MODELS to every public BaseModel in
# atlas.core.models and PLANNING_MODELS to every public BaseModel in
# atlas.planning (D1, ATLAS-23).
CORE_MODELS: tuple[type[BaseModel], ...] = (
    AcceptanceSession,
    AdmissionRun,
    AgentRun,
    ArchitectureDecisionRecord,
    Atlas280BootstrapRecoveryReceipt,
    CIHandoffReconciliation,
    ContextPack,
    DebtItem,
    DeliveryAdmissionPolicyRevision,
    Epic,
    Evidence,
    Lesson,
    OperatorActionReceipt,
    PlanRun,
    PlannedCIPendingRecovery,
    PlannerLogicalCall,
    PlannerPhysicalTransportAttempt,
    PlanningExecution,
    PlanningExecutionOutcome,
    PmSyncReceipt,
    Product,
    TickFailure,
    Ticket,
    TicketDependency,
    TicketStatusTransition,
    VerificationCheck,
)
PLANNING_MODELS: tuple[type[BaseModel], ...] = (
    Proposal,
    ProposalDependency,
    ProposalEpic,
    ProposalTicket,
)
CANONICAL_MODELS: tuple[type[BaseModel], ...] = CORE_MODELS + PLANNING_MODELS

SCHEMAS_DIR = "docs/generated/schemas"


def render_schema(model: type[BaseModel]) -> str:
    """Deterministic serialisation of one model's JSON Schema."""
    schema = model.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def expected_schemas() -> dict[str, str]:
    """Model name -> exact expected file content, for export and linting."""
    return {model.__name__: render_schema(model) for model in CANONICAL_MODELS}


def export(root: Path) -> list[Path]:
    """Write all schemas under ``root``; remove stale .json files."""
    target = root / SCHEMAS_DIR
    target.mkdir(parents=True, exist_ok=True)
    expected = expected_schemas()
    for stale in sorted(target.glob("*.json")):
        if stale.stem not in expected:
            stale.unlink()
    written = []
    for name, content in expected.items():
        path = target / f"{name}.json"
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="schemas_export",
        description="Atlas JSON Schema export (ATLAS-16)",
    )
    parser.add_argument(
        "--repo", default=".", help="repository root (default: current directory)"
    )
    args = parser.parse_args(argv)
    written = export(Path(args.repo).resolve())
    print(f"schemas-export: wrote {len(written)} schema(s) to {SCHEMAS_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
