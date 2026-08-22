"""One-time, exact-pair ATLAS-280 local mirror-recovery service.

Nothing imports this module automatically.  The sole executable consumer is
``scripts/bootstrap_atlas_280_ci_pending_mirror_recovery.py``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict

from atlas.core.models import Atlas280BootstrapRecoveryReceipt
from atlas.core.models.admission_run import AdmissionDecisionType, AdmissionRun
from atlas.core.models.atlas_280_bootstrap_recovery import (
    ATLAS_280_ADMISSION_POLICY_REVISION,
    ATLAS_280_ADMISSION_RUN_ID,
    ATLAS_280_DEBT_ITEM_ID,
    ATLAS_280_LINEAR_ID,
    ATLAS_280_PM_RECEIPT_ID,
    ATLAS_280_POLICY_FINGERPRINT,
    ATLAS_280_POLICY_REVISION,
    ATLAS_280_PUBLICATION_HEAD,
    ATLAS_280_TICKET_ID,
    ATLAS_281_LINEAR_ID,
    ATLAS_281_TICKET_ID,
)
from atlas.core.models.debt_item import AnomalyType
from atlas.core.models.pm_sync_receipt import (
    SUCCESSFUL_PM_SYNC_RESULTS,
    PmSyncReceipt,
)
from atlas.core.models.ticket import TicketStatus
from atlas.github.client import GitHubClient
from atlas.linear.client import LinearClient, LinearIssue
from atlas.linear.ownership import LinearStatusMap
from atlas.pm.delivery_snapshot import (
    LinearBoardPull,
    SnapshotIncompletenessCode,
    build_delivery_snapshot,
    delivery_policy_fingerprint,
)
from atlas.storage import (
    AdmissionCoordinationRepo,
    AdmissionRunRepo,
    Atlas280BootstrapRecoveryRepo,
    CIHandoffCoordinationRepo,
    DebtItemRepo,
    DeliveryAdmissionPolicyRepo,
    PmSyncReceiptRepo,
    TicketDependencyRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
)
from atlas.storage.atlas_280_bootstrap_recovery import EXPECTED_DEPENDENCIES
from atlas.storage.db import Database

RECOVERY_NAMESPACE = UUID("b5920924-0bbd-4ca4-ae88-c80cdd9bb2b3")


class Atlas280BootstrapCheckCode(StrEnum):
    ELIGIBLE = "eligible"
    ALREADY_RECOVERED = "already_recovered"
    EXISTING_RECOVERY_CONFLICT = "existing_recovery_conflict"
    BLOCKER_IDENTITY = "blocker_identity_mismatch"
    REPAIR_IDENTITY = "repair_ticket_identity_mismatch"
    LOCAL_STATE = "local_state_mismatch"
    DEPENDENCIES = "repair_dependencies_not_exact_and_done"
    ADMISSION_EVIDENCE = "admission_evidence_missing_or_ambiguous"
    PM_RECEIPT = "pm_receipt_missing_or_ambiguous"
    PUBLICATION = "publication_missing_ambiguous_or_mismatched"
    DEBT_ITEM = "historical_debt_item_missing_or_mismatched"
    BOARD_PULL = "board_pull_incomplete_or_malformed"
    BOARD_IDENTITY = "board_issue_identity_mismatch"
    BOARD_STATE = "board_state_mismatch"
    SNAPSHOT = "snapshot_has_unapproved_incompleteness"
    POLICY = "policy_not_exact_paused_revision_17"
    ADMISSION_FENCE = "admission_write_fence_present"
    CI_HANDOFF_FENCE = "ci_handoff_write_fence_present"
    TRANSITION_HISTORY = "conflicting_local_transition_history"
    ACCEPTED_MAIN = "accepted_main_commit_invalid"
    EXTERNAL_READ = "external_read_failed"
    PROOF_MOVED = "proof_changed_during_apply_preflight"
    STORAGE_REFUSED = "atomic_storage_revalidation_refused"


class Atlas280BootstrapProofSummary(BaseModel):
    """Bounded secret-free projection printed by CHECK."""

    model_config = ConfigDict(frozen=True)

    blocker_ticket_key: str
    repair_ticket_key: str
    admission_run_id: UUID
    pm_sync_receipt_id: UUID
    publication: str
    publication_head: str
    historical_debt_item_id: UUID
    board_fingerprint: str
    policy_revision: int
    policy_fingerprint: str
    accepted_main_commit: str
    recovery_id: UUID


class Atlas280BootstrapCheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    eligible: bool
    changed: bool = False
    already_recovered: bool = False
    reason_codes: tuple[Atlas280BootstrapCheckCode, ...]
    proof: Atlas280BootstrapProofSummary | None = None


class Atlas280BootstrapApplyResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    eligible: bool
    changed: bool
    already_recovered: bool
    reason_codes: tuple[Atlas280BootstrapCheckCode, ...]
    recovery_id: UUID | None = None


@dataclass(frozen=True)
class _EligibleProof:
    receipt: Atlas280BootstrapRecoveryReceipt
    summary: Atlas280BootstrapProofSummary


def _admission_is_exact(run: AdmissionRun) -> bool:
    admitted = [
        decision
        for decision in run.decisions
        if decision.decision is AdmissionDecisionType.ADMIT
    ]
    return (
        run.id == ATLAS_280_ADMISSION_RUN_ID
        and run.policy_revision == ATLAS_280_ADMISSION_POLICY_REVISION
        and run.selected_ticket_id == ATLAS_280_TICKET_ID
        and run.selected_ticket_key == "ATLAS-280"
        and len(admitted) == 1
        and admitted[0].ticket_id == ATLAS_280_TICKET_ID
        and admitted[0].ticket_key == "ATLAS-280"
        and admitted[0].external_linear_id == ATLAS_280_LINEAR_ID
        and not admitted[0].reasons
    )


def _pm_receipt_is_exact(receipt: PmSyncReceipt, run: AdmissionRun) -> bool:
    return (
        receipt.id == ATLAS_280_PM_RECEIPT_ID
        and receipt.product_id == run.product_id
        and receipt.started_at == run.evaluated_at
        and receipt.result in SUCCESSFUL_PM_SYNC_RESULTS
        and receipt.counters.get("admitted", 0) == 1
        and receipt.counters.get("promoted", 0) == 1
        and receipt.counters.get("stale", 0) == 0
        and receipt.counters.get("indeterminate", 0) == 0
    )


def _issue_by_id(issues: list[LinearIssue], issue_id: str) -> list[LinearIssue]:
    return [issue for issue in issues if issue.id == issue_id]


def _pull_request_head(payload: dict[str, Any]) -> str | None:
    head = payload.get("head")
    base = payload.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        return None
    sha = head.get("sha")
    repo = base.get("repo")
    full_name = repo.get("full_name") if isinstance(repo, dict) else None
    number = payload.get("number")
    if (
        number != 350
        or full_name != "derekrivers/atlas"
        or base.get("ref") != "main"
        or payload.get("state") != "open"
        or not isinstance(sha, str)
        or sha != ATLAS_280_PUBLICATION_HEAD
    ):
        return None
    return sha


def _recovery_id(payload: dict[str, Any]) -> UUID:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    return uuid5(RECOVERY_NAMESPACE, digest)


class Atlas280BootstrapRecoveryService:
    """Reconstruct exact proof and, only on explicit apply, repair the mirror."""

    def __init__(
        self,
        *,
        db: Database,
        linear: LinearClient,
        github: GitHubClient,
        status_map: LinearStatusMap,
        team_id: str,
        project_id: str,
        accepted_main_commit: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._db = db
        self._linear = linear
        self._github = github
        self._status_map = status_map
        self._team_id = team_id
        self._project_id = project_id
        self._accepted_main_commit = accepted_main_commit
        self._clock = clock

    def _already_recovered(self) -> Atlas280BootstrapCheckResult | None:
        existing = Atlas280BootstrapRecoveryRepo(self._db).get()
        if existing is None:
            return None
        blocker = TicketRepo(self._db).get_by_key("ATLAS-280")
        transitions = (
            []
            if blocker is None
            else TicketStatusTransitionRepo(self._db).list_for_ticket(blocker.id)
        )
        exact_transition = (
            len(transitions) == 1
            and transitions[0].from_status == TicketStatus.PLANNED.value
            and transitions[0].to_status == TicketStatus.CI_PENDING.value
            and transitions[0].created_by_id == "bootstrap:atlas-280-mirror-recovery"
        )
        if (
            blocker is not None
            and blocker.id == ATLAS_280_TICKET_ID
            and blocker.status is TicketStatus.CI_PENDING
            and exact_transition
        ):
            return Atlas280BootstrapCheckResult(
                eligible=False,
                changed=False,
                already_recovered=True,
                reason_codes=(Atlas280BootstrapCheckCode.ALREADY_RECOVERED,),
            )
        return Atlas280BootstrapCheckResult(
            eligible=False,
            changed=False,
            reason_codes=(Atlas280BootstrapCheckCode.EXISTING_RECOVERY_CONFLICT,),
        )

    def _evaluate(
        self, *, operator_id: str
    ) -> tuple[Atlas280BootstrapCheckResult, _EligibleProof | None]:
        already = self._already_recovered()
        if already is not None:
            return already, None

        reasons: list[Atlas280BootstrapCheckCode] = []
        if len(self._accepted_main_commit) != 40 or any(
            char not in "0123456789abcdef" for char in self._accepted_main_commit
        ):
            reasons.append(Atlas280BootstrapCheckCode.ACCEPTED_MAIN)

        tickets = TicketRepo(self._db)
        blocker = tickets.get_by_key("ATLAS-280")
        repair = tickets.get_by_key("ATLAS-281")
        if (
            blocker is None
            or blocker.id != ATLAS_280_TICKET_ID
            or blocker.external_linear_id != ATLAS_280_LINEAR_ID
        ):
            reasons.append(Atlas280BootstrapCheckCode.BLOCKER_IDENTITY)
        if (
            repair is None
            or repair.id != ATLAS_281_TICKET_ID
            or repair.external_linear_id != ATLAS_281_LINEAR_ID
        ):
            reasons.append(Atlas280BootstrapCheckCode.REPAIR_IDENTITY)
        if blocker is None or repair is None:
            return self._failed(reasons), None
        if (
            blocker.status is not TicketStatus.PLANNED
            or repair.status is not TicketStatus.PLANNED
        ):
            reasons.append(Atlas280BootstrapCheckCode.LOCAL_STATE)
        if blocker.product_id != repair.product_id:
            reasons.append(Atlas280BootstrapCheckCode.REPAIR_IDENTITY)
        product_id = blocker.product_id

        dependencies = TicketDependencyRepo(self._db).list()
        ticket_by_id = {ticket.id: ticket for ticket in tickets.list()}
        repair_dependencies = [
            dependency
            for dependency in dependencies
            if dependency.source_ticket_id == ATLAS_281_TICKET_ID
            and dependency.target_entity_type == "ticket"
            and dependency.dependency_type.value == "depends_on"
        ]
        dependency_targets = [
            ticket_by_id.get(dependency.target_entity_id)
            for dependency in repair_dependencies
        ]
        if (
            {target.key for target in dependency_targets if target is not None}
            != EXPECTED_DEPENDENCIES
            or len(repair_dependencies) != len(EXPECTED_DEPENDENCIES)
            or any(
                target is None or target.status is not TicketStatus.DONE
                for target in dependency_targets
            )
        ):
            reasons.append(Atlas280BootstrapCheckCode.DEPENDENCIES)

        policy = DeliveryAdmissionPolicyRepo(self._db).get_active(product_id)
        if (
            policy is None
            or policy.revision != ATLAS_280_POLICY_REVISION
            or policy.mode.value != "paused"
            or delivery_policy_fingerprint(policy) != ATLAS_280_POLICY_FINGERPRINT
        ):
            reasons.append(Atlas280BootstrapCheckCode.POLICY)
        if AdmissionCoordinationRepo(self._db).get_fence(product_id) is not None:
            reasons.append(Atlas280BootstrapCheckCode.ADMISSION_FENCE)
        if CIHandoffCoordinationRepo(self._db).get_fence(product_id) is not None:
            reasons.append(Atlas280BootstrapCheckCode.CI_HANDOFF_FENCE)

        runs = AdmissionRunRepo(self._db).list_for_product(product_id)
        exact_runs = [run for run in runs if _admission_is_exact(run)]
        selected_runs = [
            run
            for run in runs
            if run.selected_ticket_id == ATLAS_280_TICKET_ID
            or run.selected_ticket_key == "ATLAS-280"
        ]
        if len(exact_runs) != 1 or len(selected_runs) != 1:
            reasons.append(Atlas280BootstrapCheckCode.ADMISSION_EVIDENCE)
            run = None
        else:
            run = exact_runs[0]

        receipts = PmSyncReceiptRepo(self._db).list()
        exact_receipts = (
            []
            if run is None
            else [receipt for receipt in receipts if _pm_receipt_is_exact(receipt, run)]
        )
        corresponding_receipts = (
            []
            if run is None
            else [
                receipt
                for receipt in receipts
                if receipt.product_id == product_id
                and receipt.started_at == run.evaluated_at
                and receipt.counters.get("admitted", 0) == 1
            ]
        )
        if len(exact_receipts) != 1 or len(corresponding_receipts) != 1:
            reasons.append(Atlas280BootstrapCheckCode.PM_RECEIPT)
            pm_receipt = None
        else:
            pm_receipt = exact_receipts[0]

        debt = DebtItemRepo(self._db).get(ATLAS_280_DEBT_ITEM_ID)
        if (
            debt is None
            or debt.ticket_id != ATLAS_280_TICKET_ID
            or debt.product_id != product_id
            or debt.anomaly_type is not AnomalyType.OUT_OF_OWNERSHIP_TRANSITION
        ):
            reasons.append(Atlas280BootstrapCheckCode.DEBT_ITEM)

        if TicketStatusTransitionRepo(self._db).list_for_ticket(ATLAS_280_TICKET_ID):
            reasons.append(Atlas280BootstrapCheckCode.TRANSITION_HISTORY)

        try:
            self._status_map.validate_against_states(
                self._linear.fetch_workflow_states(self._team_id)
            )
            fetched = self._linear.fetch_project_issues(self._project_id)
        except Exception:
            reasons.append(Atlas280BootstrapCheckCode.EXTERNAL_READ)
            return self._failed(reasons), None

        board_pull = LinearBoardPull(
            issues=tuple(fetched),
            complete=bool(getattr(fetched, "complete", True)),
            pagination_gaps=tuple(getattr(fetched, "pagination_gaps", ())),
        )
        if not board_pull.complete or board_pull.pagination_gaps:
            reasons.append(Atlas280BootstrapCheckCode.BOARD_PULL)

        blocker_issues = _issue_by_id(fetched, ATLAS_280_LINEAR_ID)
        repair_issues = _issue_by_id(fetched, ATLAS_281_LINEAR_ID)
        if (
            len(blocker_issues) != 1
            or len(repair_issues) != 1
            or blocker_issues[0].identifier != "ATL-456"
            or repair_issues[0].identifier != "ATL-457"
        ):
            reasons.append(Atlas280BootstrapCheckCode.BOARD_IDENTITY)
        blocker_issue = blocker_issues[0] if len(blocker_issues) == 1 else None
        repair_issue = repair_issues[0] if len(repair_issues) == 1 else None
        if (
            blocker_issue is None
            or repair_issue is None
            or self._status_map.status_for(blocker_issue.state_id)
            is not TicketStatus.CI_PENDING
            or self._status_map.status_for(repair_issue.state_id)
            is not TicketStatus.PLANNED
        ):
            reasons.append(Atlas280BootstrapCheckCode.BOARD_STATE)

        snapshot = None
        if policy is not None:
            try:
                snapshot = build_delivery_snapshot(
                    product_id=product_id,
                    linear_project_id=self._project_id,
                    policy=policy,
                    status_map=self._status_map,
                    board_pull=board_pull,
                    tickets=ticket_by_id.values(),
                    dependencies=dependencies,
                    clock=self._clock,
                )
            except Exception:
                reasons.append(Atlas280BootstrapCheckCode.SNAPSHOT)
        if snapshot is not None:
            expected_reason = (
                len(snapshot.incompleteness_reasons) == 1
                and snapshot.incompleteness_reasons[0].code
                is SnapshotIncompletenessCode.ATLAS_LINEAR_STATE_MISMATCH
                and snapshot.incompleteness_reasons[0].ticket_key == "ATLAS-280"
                and snapshot.incompleteness_reasons[0].issue_id == ATLAS_280_LINEAR_ID
                and blocker_issue is not None
                and snapshot.incompleteness_reasons[0].state_id
                == blocker_issue.state_id
            )
            if not expected_reason or snapshot.over_capacity:
                reasons.append(Atlas280BootstrapCheckCode.SNAPSHOT)

        publication_head = None
        if blocker_issue is not None:
            publications = blocker_issue.github_publications
            if (
                not blocker_issue.github_publications_complete
                or len(publications) != 1
                or publications[0].repository_owner != "derekrivers"
                or publications[0].repository_name != "atlas"
                or publications[0].pr_number != 350
            ):
                reasons.append(Atlas280BootstrapCheckCode.PUBLICATION)
            else:
                try:
                    publication_head = _pull_request_head(
                        self._github.fetch_pull_request("derekrivers", "atlas", 350)
                    )
                except Exception:
                    publication_head = None
                if publication_head is None:
                    reasons.append(Atlas280BootstrapCheckCode.PUBLICATION)

        if reasons:
            return self._failed(reasons), None
        assert policy is not None
        assert run is not None
        assert pm_receipt is not None
        assert blocker_issue is not None
        assert repair_issue is not None
        assert snapshot is not None
        assert publication_head is not None

        identity_payload = {
            "schema_version": "atlas-280-bootstrap-mirror-recovery-v1",
            "blocker_ticket_id": str(ATLAS_280_TICKET_ID),
            "repair_ticket_id": str(ATLAS_281_TICKET_ID),
            "admission_run_id": str(ATLAS_280_ADMISSION_RUN_ID),
            "pm_sync_receipt_id": str(ATLAS_280_PM_RECEIPT_ID),
            "publication_head": publication_head,
            "historical_debt_item_id": str(ATLAS_280_DEBT_ITEM_ID),
            "board_fingerprint": snapshot.fetched_board_fingerprint,
            "policy_id": str(policy.id),
            "policy_fingerprint": ATLAS_280_POLICY_FINGERPRINT,
            "accepted_main_commit": self._accepted_main_commit,
        }
        recovery_id = _recovery_id(identity_payload)
        receipt = Atlas280BootstrapRecoveryReceipt(
            id=recovery_id,
            product_id=product_id,
            blocker_ticket_id=ATLAS_280_TICKET_ID,
            blocker_linear_issue_id=ATLAS_280_LINEAR_ID,
            blocker_linear_state_id=blocker_issue.state_id or "",
            repair_ticket_id=ATLAS_281_TICKET_ID,
            repair_linear_issue_id=ATLAS_281_LINEAR_ID,
            repair_linear_state_id=repair_issue.state_id or "",
            admission_run_id=ATLAS_280_ADMISSION_RUN_ID,
            pm_sync_receipt_id=ATLAS_280_PM_RECEIPT_ID,
            publication_head=publication_head,
            historical_debt_item_id=ATLAS_280_DEBT_ITEM_ID,
            board_fingerprint=snapshot.fetched_board_fingerprint,
            policy_id=policy.id,
            policy_fingerprint=ATLAS_280_POLICY_FINGERPRINT,
            accepted_main_commit=self._accepted_main_commit,
            created_at=self._clock(),
            created_by_id=operator_id,
        )
        summary = Atlas280BootstrapProofSummary(
            blocker_ticket_key="ATLAS-280",
            repair_ticket_key="ATLAS-281",
            admission_run_id=ATLAS_280_ADMISSION_RUN_ID,
            pm_sync_receipt_id=ATLAS_280_PM_RECEIPT_ID,
            publication="derekrivers/atlas#350",
            publication_head=publication_head,
            historical_debt_item_id=ATLAS_280_DEBT_ITEM_ID,
            board_fingerprint=snapshot.fetched_board_fingerprint,
            policy_revision=ATLAS_280_POLICY_REVISION,
            policy_fingerprint=ATLAS_280_POLICY_FINGERPRINT,
            accepted_main_commit=self._accepted_main_commit,
            recovery_id=recovery_id,
        )
        result = Atlas280BootstrapCheckResult(
            eligible=True,
            reason_codes=(Atlas280BootstrapCheckCode.ELIGIBLE,),
            proof=summary,
        )
        return result, _EligibleProof(receipt=receipt, summary=summary)

    @staticmethod
    def _failed(
        reasons: list[Atlas280BootstrapCheckCode],
    ) -> Atlas280BootstrapCheckResult:
        return Atlas280BootstrapCheckResult(
            eligible=False,
            changed=False,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )

    def check(self) -> Atlas280BootstrapCheckResult:
        """Reconstruct the proof with zero mutation."""

        try:
            result, _ = self._evaluate(operator_id="operator-check")
            return result
        except Exception:
            return self._failed([Atlas280BootstrapCheckCode.STORAGE_REFUSED])

    def apply(self, *, operator_id: str) -> Atlas280BootstrapApplyResult:
        """Revalidate twice, then invoke the atomic local-only repository seam."""

        try:
            first, first_proof = self._evaluate(operator_id=operator_id)
        except Exception:
            return Atlas280BootstrapApplyResult(
                eligible=False,
                changed=False,
                already_recovered=False,
                reason_codes=(Atlas280BootstrapCheckCode.STORAGE_REFUSED,),
            )
        if first.already_recovered:
            existing = Atlas280BootstrapRecoveryRepo(self._db).get()
            return Atlas280BootstrapApplyResult(
                eligible=False,
                changed=False,
                already_recovered=True,
                reason_codes=first.reason_codes,
                recovery_id=None if existing is None else existing.id,
            )
        if not first.eligible or first_proof is None:
            return Atlas280BootstrapApplyResult(
                eligible=False,
                changed=False,
                already_recovered=False,
                reason_codes=first.reason_codes,
            )

        try:
            second, second_proof = self._evaluate(operator_id=operator_id)
        except Exception:
            return Atlas280BootstrapApplyResult(
                eligible=False,
                changed=False,
                already_recovered=False,
                reason_codes=(Atlas280BootstrapCheckCode.PROOF_MOVED,),
            )
        if (
            not second.eligible
            or second_proof is None
            or first_proof.receipt.id != second_proof.receipt.id
            or first_proof.receipt.board_fingerprint
            != second_proof.receipt.board_fingerprint
        ):
            return Atlas280BootstrapApplyResult(
                eligible=False,
                changed=False,
                already_recovered=False,
                reason_codes=(Atlas280BootstrapCheckCode.PROOF_MOVED,),
            )
        try:
            applied = Atlas280BootstrapRecoveryRepo(self._db).apply(
                second_proof.receipt
            )
        except Exception:
            return Atlas280BootstrapApplyResult(
                eligible=False,
                changed=False,
                already_recovered=False,
                reason_codes=(Atlas280BootstrapCheckCode.STORAGE_REFUSED,),
            )
        return Atlas280BootstrapApplyResult(
            eligible=True,
            changed=applied.changed,
            already_recovered=not applied.changed,
            reason_codes=(Atlas280BootstrapCheckCode.ELIGIBLE,),
            recovery_id=applied.receipt.id,
        )
