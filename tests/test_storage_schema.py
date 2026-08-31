"""ATLAS-18: DDL contracts per data-model §3.1-§3.10 SQL blocks.

Expected tables are transcribed from the documented SQL — column names,
nullability, DEFAULT clauses, FK targets, UNIQUE and CHECK constraints —
not derived from the ORM, so drift in either direction fails. Plus: the
Alembic baseline matches the ORM metadata, and the full DDL compiles
under the PostgreSQL dialect (the honesty mechanism for a SQLite-only
CI; compile-compatibility is the stated limit of the claim).
"""

import hashlib
import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql

from atlas.pm import delivery_policy_fingerprint
from atlas.storage import AdmissionRunRepo, DeliveryAdmissionPolicyRepo
from atlas.storage.db import Database
from atlas.storage.tables import Base

REPO_ROOT = Path(__file__).resolve().parent.parent

# Transcribed from the §3 SQL blocks: column -> (nullable, default literal).
NN = False  # NOT NULL
DOCUMENTED_COLUMNS: dict[str, dict[str, tuple[bool, str | None]]] = {
    # §3.1
    "products": {
        "id": (NN, None),
        "key": (NN, None),
        "name": (NN, None),
        "description": (NN, None),
        "vision": (NN, None),
        "status": (NN, None),
        "goals": (NN, "'[]'"),
        "non_goals": (NN, "'[]'"),
        "constraints": (NN, "'[]'"),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
        "updated_at": (NN, None),
        "archived_at": (True, None),
    },
    # §3.2
    "architecture_decision_records": {
        "id": (NN, None),
        "product_id": (NN, None),
        "number": (NN, None),
        "title": (NN, None),
        "status": (NN, None),
        "context": (NN, None),
        "decision": (NN, None),
        "rationale": (NN, None),
        "consequences": (NN, "'[]'"),
        "alternatives_considered": (NN, "'[]'"),
        "supersedes_adr_id": (True, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
        "updated_at": (NN, None),
    },
    # §3.3
    "epics": {
        "id": (NN, None),
        "product_id": (NN, None),
        "key": (NN, None),
        "title": (NN, None),
        "description": (NN, None),
        "objective": (NN, None),
        "status": (NN, None),
        "priority": (NN, "0"),
        "risk_level": (NN, None),
        "source_anchor": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
        "updated_at": (NN, None),
        "completed_at": (True, None),
    },
    # §3.4
    "tickets": {
        "id": (NN, None),
        "product_id": (NN, None),
        "epic_id": (True, None),
        "key": (NN, None),
        "title": (NN, None),
        "objective": (NN, None),
        "context": (NN, None),
        "status": (NN, None),
        "ticket_type": (NN, None),
        "risk_level": (NN, None),
        "priority": (NN, "0"),
        "relevant_docs": (NN, "'[]'"),
        "tags": (NN, "'[]'"),
        "component": (True, None),
        "acceptance_criteria": (NN, "'[]'"),
        "non_goals": (NN, "'[]'"),
        "implementation_notes": (NN, "'[]'"),
        "test_requirements": (NN, "'[]'"),
        "documentation_requirements": (NN, "'[]'"),
        "definition_of_done": (NN, "'[]'"),
        "estimated_effort": (True, None),
        "external_linear_id": (True, None),
        "external_github_issue_id": (True, None),
        "linear_synced_at": (True, None),
        "last_observed_linear_state_id": (True, None),
        "status_entered_at": (True, None),
        "review_cycle_count": (NN, "0"),
        "lesson_extraction_attempted_at": (True, None),
        "source_anchor": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
        "updated_at": (NN, None),
        "completed_at": (True, None),
    },
    # §3.5 (post-chore: attribution columns)
    "ticket_dependencies": {
        "id": (NN, None),
        "source_ticket_id": (NN, None),
        "target_entity_type": (NN, None),
        "target_entity_id": (NN, None),
        "dependency_type": (NN, None),
        "reason": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
    },
    # §3.6 (post-ATLAS-99: confidence nullable until operator promotion)
    "lessons": {
        "id": (NN, None),
        "product_id": (NN, None),
        "status": (NN, "'draft'"),
        "category": (NN, None),
        "title": (NN, None),
        "problem": (NN, None),
        "solution": (NN, None),
        "outcome": (NN, None),
        "confidence": (True, None),
        "source_ticket_id": (NN, None),
        "related_ticket_ids": (NN, "'[]'"),
        "related_adr_ids": (NN, "'[]'"),
        "tags": (NN, "'[]'"),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
        "updated_at": (NN, None),
    },
    # §3.7
    "evidence": {
        "id": (NN, None),
        "product_id": (NN, None),
        "ticket_id": (True, None),
        "agent_run_id": (True, None),
        "evidence_type": (NN, None),
        "status": (NN, None),
        "summary": (NN, None),
        "commit_sha": (True, None),
        "external_run_id": (True, None),
        "job_name": (True, None),
        "source_event_at": (True, None),
        "payload_hash": (True, None),
        "source_uri": (True, None),
        "raw_payload": (NN, "'{}'"),
        "docs_paths": (True, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
    },
    # §3.8
    "agent_runs": {
        "id": (NN, None),
        "product_id": (NN, None),
        "ticket_id": (True, None),
        "provider": (NN, None),
        "model": (True, None),
        "status": (NN, None),
        "objective": (NN, None),
        "input_context_pack_id": (True, None),
        "output_summary": (True, None),
        "error_summary": (True, None),
        "cost_estimate_usd": (True, None),
        "prompt_tokens": (True, None),
        "completion_tokens": (True, None),
        "started_at": (True, None),
        "completed_at": (True, None),
        "created_at": (NN, None),
    },
    # §3.9
    "context_packs": {
        "id": (NN, None),
        "product_id": (NN, None),
        "ticket_id": (True, None),
        "title": (NN, None),
        "objective": (NN, None),
        "constraints": (NN, "'[]'"),
        "relevant_docs": (NN, "'[]'"),
        "relevant_adrs": (NN, "'[]'"),
        "related_tickets": (NN, "'[]'"),
        "historical_lessons": (NN, "'[]'"),
        "acceptance_criteria": (NN, "'[]'"),
        "risks": (NN, "'[]'"),
        "test_commands": (NN, "'[]'"),
        "definition_of_done": (NN, "'[]'"),
        "rendered_markdown": (NN, None),
        "compression_applied": (NN, "'[]'"),
        "input_doc_shas": (NN, "'{}'"),
        "token_estimate": (True, None),
        "created_at": (NN, None),
    },
    # §3.10
    "plan_runs": {
        "id": (NN, None),
        "product_id": (NN, None),
        "status": (NN, None),
        "input_doc_shas": (NN, "'{}'"),
        "model_provider": (NN, None),
        "model_name": (NN, None),
        "prompt_version": (NN, None),
        "prompt_hash": (NN, None),
        "model_parameters": (NN, "'{}'"),
        "similarity_threshold": (NN, None),
        "raw_output_hash": (NN, None),
        "proposal": (NN, "'{}'"),
        "generation_stages": (NN, "'[]'"),
        "diff_summary": (NN, "'{}'"),
        "failure_reason": (True, None),
        "approved_by": (True, None),
        "created_at": (NN, None),
        "applied_at": (True, None),
    },
    # §3.12
    "key_counters": {
        "prefix": (NN, None),
        "high_water": (NN, "0"),
    },
    # §6.2 (delivery-anomaly record, ATLAS-116): append-only — no status,
    # no updated_at.
    "debt_items": {
        "id": (NN, None),
        "product_id": (NN, None),
        "ticket_id": (NN, None),
        "anomaly_type": (NN, None),
        "summary": (NN, None),
        "observed_at": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
    },
    # §6.4 (tick-failure record, ATLAS-125): append-only — no status, no
    # updated_at. Tick-level: NO ticket_id and NO product_id, so NO FK.
    "tick_failures": {
        "id": (NN, None),
        "occurred_at": (NN, None),
        "failure_signature": (NN, None),
        "detail": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
    },
    # §6.8 (PM sync receipt, ATLAS-245): append-only completion-boundary
    # receipt. Stores fingerprints and counters, never raw Linear payloads.
    "pm_sync_receipts": {
        "id": (NN, None),
        "product_id": (True, None),
        "product_key": (True, None),
        "linear_project_id": (NN, None),
        "started_at": (NN, None),
        "finished_at": (NN, None),
        "status_map_fingerprint": (NN, None),
        "fetched_board_fingerprint": (NN, None),
        "fetched_board_issue_count": (NN, None),
        "result": (NN, None),
        "counters": (NN, "'{}'"),
        "error_summary": (True, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
    },
    # §6.6 (status-transition record, ATLAS-121): append-only — no status, no
    # created_at, no updated_at. Ticket-scoped: ticket_id is FK-backed (modelled
    # on debt_items), but there is NO product_id.
    "ticket_status_transitions": {
        "id": (NN, None),
        "ticket_id": (NN, None),
        "from_status": (NN, None),
        "to_status": (NN, None),
        "occurred_at": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
    },
    # §5.2 (verification-check record, ATLAS-71): NOT evidence — status is an
    # EvidenceStatus outcome but there is no trust tier and no commit pin.
    # Append-only — no updated_at; completed_at is nullable. required defaults
    # TRUE; evidence_ids defaults '[]'. ticket_id is FK-backed and NOT NULL.
    "verification_checks": {
        "id": (NN, None),
        "ticket_id": (NN, None),
        "check_type": (NN, None),
        "status": (NN, None),
        "summary": (NN, None),
        "required": (NN, "TRUE"),
        "evidence_ids": (NN, "'[]'"),
        "created_at": (NN, None),
        "completed_at": (True, None),
    },
    # §5.4 idempotency-key reservation: append-only, internal gateway state.
    "operator_action_keys": {
        "idempotency_key_identity": (NN, None),
        "request_fingerprint": (NN, None),
        "receipt_id": (NN, None),
        "correlation_id": (NN, None),
        "action": (NN, None),
        "target_type": (NN, None),
        "target_id": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
    },
    # §5.5 terminal operator-action receipt: append-only; no updated_at.
    "operator_action_receipts": {
        "id": (NN, None),
        "correlation_id": (NN, None),
        "action": (NN, None),
        "target_type": (NN, None),
        "target_id": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "idempotency_key_identity": (NN, None),
        "request_fingerprint": (NN, None),
        "outcome": (NN, None),
        "result_code": (NN, None),
        "result_metadata": (NN, "'{}'"),
        "before_status": (True, None),
        "after_status": (True, None),
        "created_at": (NN, None),
        "completed_at": (NN, None),
    },
    # §5.14 complete immutable safe result for successful lesson disposition.
    "lesson_disposition_result_snapshots": {
        "idempotency_key_identity": (NN, None),
        "id": (NN, None),
        "product_id": (NN, None),
        "status": (NN, None),
        "category": (NN, None),
        "title": (NN, None),
        "problem": (NN, None),
        "solution": (NN, None),
        "outcome": (NN, None),
        "confidence": (True, None),
        "source_ticket_id": (NN, None),
        "related_ticket_ids": (NN, None),
        "related_adr_ids": (NN, None),
        "tags": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
        "updated_at": (NN, None),
    },
    # §5.8 acceptance session: pinned identity plus append-oriented summaries.
    "acceptance_sessions": {
        "id": (NN, None),
        "repository_owner": (NN, None),
        "repository_name": (NN, None),
        "pr_number": (NN, None),
        "close_set": (NN, None),
        "head_ref": (NN, None),
        "head_sha": (NN, None),
        "head_repository": (NN, None),
        "base_ref": (NN, None),
        "base_sha": (NN, None),
        "base_repository": (NN, None),
        "initial_assessment": (NN, None),
        "criteria_snapshot": (NN, None),
        "criteria_fingerprint": (NN, None),
        "creation_idempotency_key_identity": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "lifecycle": (NN, None),
        "step_summaries": (NN, None),
        "blocking_reasons": (NN, None),
        "stored_merge_ready": (NN, "FALSE"),
        "historical_readiness_reasons": (NN, None),
        "created_at": (NN, None),
        "updated_at": (NN, None),
        "staled_at": (True, None),
    },
    # §5.10 immutable delivery-admission policy revision.
    "delivery_admission_policy_revisions": {
        "id": (NN, None),
        "product_id": (NN, None),
        "revision": (NN, None),
        "mode": (NN, None),
        "approved_symphony_ceiling": (NN, None),
        "working_budget": (NN, None),
        "integration_budget": (NN, "1"),
        "review_budget": (NN, None),
        "changes_requested_reserve": (NN, None),
        "risk_lane_limits": (NN, "'[]'"),
        "component_lane_limits": (NN, "'[]'"),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
    },
    # §5.12 one mutable active pointer per product.
    "delivery_admission_policy_active": {
        "product_id": (NN, None),
        "revision": (NN, None),
    },
    # §5.14 immutable deterministic admission evaluation.
    "admission_runs": {
        "id": (NN, None),
        "schema_version": (NN, None),
        "product_id": (NN, None),
        "policy_id": (NN, None),
        "policy_revision": (NN, None),
        "policy_fingerprint": (NN, None),
        "snapshot_fingerprint": (NN, None),
        "snapshot_observed_at": (NN, None),
        "evaluated_at": (NN, None),
        "selected_ticket_id": (True, None),
        "selected_ticket_key": (True, None),
        "decisions": (NN, "'[]'"),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
    },
    # §5.18 one-time exact-pair ATLAS-280 bootstrap recovery proof.
    "atlas_280_bootstrap_recovery_receipts": {
        "id": (NN, None),
        "schema_version": (NN, None),
        "product_id": (NN, None),
        "blocker_ticket_id": (NN, None),
        "blocker_ticket_key": (NN, None),
        "blocker_linear_issue_id": (NN, None),
        "blocker_linear_identifier": (NN, None),
        "blocker_linear_state_id": (NN, None),
        "repair_ticket_id": (NN, None),
        "repair_ticket_key": (NN, None),
        "repair_linear_issue_id": (NN, None),
        "repair_linear_identifier": (NN, None),
        "repair_linear_state_id": (NN, None),
        "source_local_status": (NN, None),
        "recovered_local_status": (NN, None),
        "admission_run_id": (NN, None),
        "pm_sync_receipt_id": (NN, None),
        "publication_repository_owner": (NN, None),
        "publication_repository_name": (NN, None),
        "publication_pr_number": (NN, None),
        "publication_head": (NN, None),
        "historical_debt_item_id": (NN, None),
        "board_fingerprint": (NN, None),
        "policy_id": (NN, None),
        "policy_revision": (NN, None),
        "policy_fingerprint": (NN, None),
        "accepted_main_commit": (NN, None),
        "created_at": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
    },
    # §5.19 reusable evidence-backed local mirror recovery proof.
    "planned_ci_pending_recoveries": {
        "id": (NN, None),
        "schema_version": (NN, None),
        "product_id": (NN, None),
        "ticket_id": (NN, None),
        "ticket_key": (NN, None),
        "linear_issue_id": (NN, None),
        "linear_project_id": (NN, None),
        "observed_linear_state_id": (NN, None),
        "source_local_status": (NN, None),
        "recovered_local_status": (NN, None),
        "admission_run_id": (NN, None),
        "pm_sync_receipt_id": (NN, None),
        "publication_attachment_id": (NN, None),
        "publication_repository_owner": (NN, None),
        "publication_repository_name": (NN, None),
        "publication_pr_number": (NN, None),
        "board_fingerprint": (NN, None),
        "board_issue_count": (NN, None),
        "observed_at": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
    },
    # §5.20 dormant PM recovery/fairness and bounded blocker state.
    "pm_recovery_sequence_counters": {
        "product_id": (NN, None),
        "high_water": (NN, "0"),
    },
    "pm_recovery_episodes": {
        "id": (NN, None),
        "schema_version": (NN, None),
        "identity_fingerprint": (NN, None),
        "product_id": (NN, None),
        "operation": (NN, None),
        "authority_id": (NN, None),
        "authoritative_episode_id": (NN, None),
        "active_scope_fingerprint": (True, None),
        "candidate_ticket_id": (True, None),
        "candidate_ticket_key": (True, None),
        "episode_created_sequence": (NN, None),
        "last_evaluated_sequence": (True, None),
        "last_evaluation_id": (True, None),
        "last_evaluation_fingerprint": (True, None),
        "created_at": (NN, None),
        "last_evaluated_at": (True, None),
        "closed_at": (True, None),
        "closure_event_id": (True, None),
        "closure_kind": (True, None),
        "replaces_episode_id": (True, None),
        "replacement_event_id": (True, None),
    },
    "pm_blocker_occurrences": {
        "id": (NN, None),
        "schema_version": (NN, None),
        "product_id": (NN, None),
        "operation": (NN, None),
        "code": (NN, None),
        "kind": (NN, None),
        "authority_kind": (NN, None),
        "authority_id": (NN, None),
        "recovery_episode_id": (NN, None),
        "candidate_ticket_id": (True, None),
        "candidate_ticket_key": (True, None),
        "blocker_fingerprint": (NN, None),
        "active_fingerprint": (True, None),
        "first_evaluation_id": (NN, None),
        "latest_evaluation_id": (NN, None),
        "first_observed_at": (NN, None),
        "latest_observed_at": (NN, None),
        "consecutive_observations": (NN, None),
        "next_safe_retry_at": (True, None),
        "capacity_impact": (NN, "FALSE"),
        "starved_candidates_truncated": (NN, "FALSE"),
        "policy_namespace": (True, None),
        "policy_revision": (True, None),
        "policy_fingerprint": (True, None),
        "superseded_at": (True, None),
        "superseded_by_event_id": (True, None),
        "supersession_kind": (True, None),
    },
    "pm_blocker_starved_candidates": {
        "blocker_occurrence_id": (NN, None),
        "ordinal": (NN, None),
        "ticket_id": (NN, None),
        "ticket_key": (NN, None),
        "started_at": (NN, None),
    },
    # Phase-15 single-write admission coordination state.
    "admission_leases": {
        "product_id": (NN, None),
        "owner_id": (NN, None),
        "acquired_at": (NN, None),
        "expires_at": (NN, None),
    },
    "admission_eligibility": {
        "ticket_id": (NN, None),
        "product_id": (NN, None),
        "continuously_eligible_since": (NN, None),
    },
    "admission_write_fences": {
        "product_id": (NN, None),
        "admission_run_id": (NN, None),
        "ticket_id": (NN, None),
        "ticket_key": (NN, None),
        "issue_id": (NN, None),
        "source_state_id": (NN, None),
        "target_state_id": (NN, None),
        "policy_revision": (NN, None),
        "state": (NN, None),
        "created_at": (NN, None),
        "updated_at": (NN, None),
    },
    # §6.10 immutable exact-head CI handoff outcome plus its crash-safe
    # single-write coordination fence.
    "ci_handoff_reconciliations": {
        "id": (NN, None),
        "schema_version": (NN, None),
        "product_id": (NN, None),
        "ticket_id": (NN, None),
        "ticket_key": (NN, None),
        "linear_issue_id": (True, None),
        "repository_owner": (NN, None),
        "repository_name": (NN, None),
        "pr_number": (NN, None),
        "head_commit": (NN, None),
        "policy_id": (True, None),
        "policy_revision": (True, None),
        "policy_fingerprint": (True, None),
        "snapshot_fingerprint": (True, None),
        "classification": (NN, None),
        "reason": (NN, None),
        "decision": (NN, None),
        "check_results": (NN, "'[]'"),
        "observed_at": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
    },
    "ci_handoff_write_fences": {
        "product_id": (NN, None),
        "reconciliation_id": (NN, None),
        "ticket_id": (NN, None),
        "ticket_key": (NN, None),
        "issue_id": (NN, None),
        "source_state_id": (NN, None),
        "target_state_id": (NN, None),
        "target_status": (NN, None),
        "state": (NN, None),
        "created_at": (NN, None),
        "updated_at": (NN, None),
    },
}

# Transcribed FK targets: table -> {column: referred table}. Absence is
# contractual too (agent_run_id, input_context_pack_id).
DOCUMENTED_FOREIGN_KEYS: dict[str, dict[str, str]] = {
    "products": {},
    "architecture_decision_records": {
        "product_id": "products",
        "supersedes_adr_id": "architecture_decision_records",
    },
    "epics": {"product_id": "products"},
    "tickets": {"product_id": "products", "epic_id": "epics"},
    "ticket_dependencies": {"source_ticket_id": "tickets"},
    "lessons": {"product_id": "products"},
    "evidence": {"product_id": "products", "ticket_id": "tickets"},
    "agent_runs": {"product_id": "products", "ticket_id": "tickets"},
    "context_packs": {"product_id": "products", "ticket_id": "tickets"},
    "plan_runs": {"product_id": "products"},
    "key_counters": {},
    "debt_items": {"product_id": "products", "ticket_id": "tickets"},
    "tick_failures": {},
    "pm_sync_receipts": {"product_id": "products"},
    "ticket_status_transitions": {"ticket_id": "tickets"},
    "verification_checks": {"ticket_id": "tickets"},
    "operator_action_keys": {},
    "operator_action_receipts": {"idempotency_key_identity": "operator_action_keys"},
    "lesson_disposition_result_snapshots": {
        "idempotency_key_identity": "operator_action_keys"
    },
    "acceptance_sessions": {},
    "delivery_admission_policy_revisions": {"product_id": "products"},
    "delivery_admission_policy_active": {
        "product_id": "delivery_admission_policy_revisions"
    },
    "admission_runs": {
        "product_id": "products",
        "policy_id": "delivery_admission_policy_revisions",
        "selected_ticket_id": "tickets",
    },
    "atlas_280_bootstrap_recovery_receipts": {
        "product_id": "products",
        "blocker_ticket_id": "tickets",
        "repair_ticket_id": "tickets",
        "admission_run_id": "admission_runs",
        "pm_sync_receipt_id": "pm_sync_receipts",
        "historical_debt_item_id": "debt_items",
        "policy_id": "delivery_admission_policy_revisions",
    },
    "planned_ci_pending_recoveries": {
        "product_id": "products",
        "ticket_id": "tickets",
        "admission_run_id": "admission_runs",
        "pm_sync_receipt_id": "pm_sync_receipts",
    },
    "pm_recovery_sequence_counters": {"product_id": "products"},
    "pm_recovery_episodes": {
        "product_id": "products",
        "candidate_ticket_id": "tickets",
        "replaces_episode_id": "pm_recovery_episodes",
    },
    "pm_blocker_occurrences": {
        "product_id": "products",
        "recovery_episode_id": "pm_recovery_episodes",
        "candidate_ticket_id": "tickets",
    },
    "pm_blocker_starved_candidates": {
        "blocker_occurrence_id": "pm_blocker_occurrences",
        "ticket_id": "tickets",
    },
    "admission_leases": {"product_id": "products"},
    "admission_eligibility": {
        "ticket_id": "tickets",
        "product_id": "products",
    },
    "admission_write_fences": {
        "product_id": "products",
        "admission_run_id": "admission_runs",
        "ticket_id": "tickets",
    },
    "ci_handoff_reconciliations": {
        "product_id": "products",
        "ticket_id": "tickets",
        "policy_id": "delivery_admission_policy_revisions",
    },
    "ci_handoff_write_fences": {
        "product_id": "products",
        "reconciliation_id": "ci_handoff_reconciliations",
        "ticket_id": "tickets",
    },
}

DOCUMENTED_UNIQUES: dict[str, list[list[str]]] = {
    "products": [["key"]],
    "architecture_decision_records": [["product_id", "number"]],
    "epics": [["key"]],
    "tickets": [["key"]],
    "operator_action_receipts": [
        ["idempotency_key_identity"],
        ["correlation_id"],
    ],
    "acceptance_sessions": [["creation_idempotency_key_identity"]],
    "delivery_admission_policy_revisions": [["product_id", "revision"]],
    "admission_runs": [],
    "atlas_280_bootstrap_recovery_receipts": [
        ["admission_run_id"],
        ["blocker_ticket_id"],
        ["pm_sync_receipt_id"],
    ],
    "planned_ci_pending_recoveries": [
        ["admission_run_id"],
        ["pm_sync_receipt_id"],
        ["ticket_id"],
    ],
    "pm_recovery_sequence_counters": [],
    "pm_recovery_episodes": [
        ["identity_fingerprint"],
        ["active_scope_fingerprint", "product_id"],
        ["replaces_episode_id"],
        ["episode_created_sequence", "product_id"],
        ["last_evaluated_sequence", "product_id"],
    ],
    "pm_blocker_occurrences": [["active_fingerprint", "product_id"]],
    "pm_blocker_starved_candidates": [
        ["blocker_occurrence_id", "ticket_id"],
        ["blocker_occurrence_id", "ticket_key"],
    ],
    "admission_leases": [],
    "admission_eligibility": [],
    "admission_write_fences": [["admission_run_id"]],
    "ci_handoff_reconciliations": [],
    "ci_handoff_write_fences": [["reconciliation_id"]],
}


@pytest.fixture
def migrated_db(tmp_path: Path) -> Database:
    db = Database(f"sqlite:///{tmp_path}/atlas.db")
    db.create_all()
    return db


def _alembic_config(url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(REPO_ROOT / "atlas" / "storage" / "migrations")
    )
    config.set_main_option("sqlalchemy.url", url)
    return config


def _uuid_text(value: object) -> str:
    return str(UUID(str(value)))


def test_documented_tables_exactly(migrated_db: Database) -> None:
    inspector = sa.inspect(migrated_db.engine)
    assert set(inspector.get_table_names()) == set(DOCUMENTED_COLUMNS)


@pytest.mark.parametrize("table", sorted(DOCUMENTED_COLUMNS), ids=str)
def test_columns_nullability_defaults(migrated_db: Database, table: str) -> None:
    inspector = sa.inspect(migrated_db.engine)
    actual = {
        column["name"]: (bool(column["nullable"]), column["default"])
        for column in inspector.get_columns(table)
    }
    assert actual == DOCUMENTED_COLUMNS[table]


@pytest.mark.parametrize("table", sorted(DOCUMENTED_FOREIGN_KEYS), ids=str)
def test_foreign_keys_exactly(migrated_db: Database, table: str) -> None:
    inspector = sa.inspect(migrated_db.engine)
    actual = {
        fk["constrained_columns"][0]: fk["referred_table"]
        for fk in inspector.get_foreign_keys(table)
    }
    assert actual == DOCUMENTED_FOREIGN_KEYS[table]


def test_fkless_columns_are_deliberate(migrated_db: Database) -> None:
    # data-model §3.7/§3.8: Phase 8 reconstructs agent runs from
    # observation; these columns carry no referential constraint.
    inspector = sa.inspect(migrated_db.engine)
    evidence_fk_columns = {
        fk["constrained_columns"][0] for fk in inspector.get_foreign_keys("evidence")
    }
    agent_run_fk_columns = {
        fk["constrained_columns"][0] for fk in inspector.get_foreign_keys("agent_runs")
    }
    assert "agent_run_id" not in evidence_fk_columns
    assert "input_context_pack_id" not in agent_run_fk_columns


@pytest.mark.parametrize("table", sorted(DOCUMENTED_UNIQUES), ids=str)
def test_unique_constraints(migrated_db: Database, table: str) -> None:
    inspector = sa.inspect(migrated_db.engine)
    actual = [
        sorted(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(table)
    ]
    for expected in DOCUMENTED_UNIQUES[table]:
        assert sorted(expected) in actual


def test_lessons_confidence_check_constraint(migrated_db: Database) -> None:
    inspector = sa.inspect(migrated_db.engine)
    checks = " ".join(
        constraint["sqltext"]
        for constraint in inspector.get_check_constraints("lessons")
    )
    assert "confidence >= 0" in checks
    assert "confidence <= 1" in checks


def test_key_counters_high_water_check_constraint(migrated_db: Database) -> None:
    # §3.12: high_water is non-negative — the monotonic-from-zero floor.
    inspector = sa.inspect(migrated_db.engine)
    checks = " ".join(
        constraint["sqltext"]
        for constraint in inspector.get_check_constraints("key_counters")
    )
    assert "high_water >= 0" in checks


def test_key_counters_primary_key_is_prefix(migrated_db: Database) -> None:
    # §3.12 / gap 1: row identity is the prefix alone — one authoritative
    # counter per prefix, which is what makes no-reuse structural.
    inspector = sa.inspect(migrated_db.engine)
    pk = inspector.get_pk_constraint("key_counters")
    assert pk["constrained_columns"] == ["prefix"]


def test_operator_action_keys_primary_key_is_idempotency_identity(
    migrated_db: Database,
) -> None:
    inspector = sa.inspect(migrated_db.engine)
    pk = inspector.get_pk_constraint("operator_action_keys")
    assert pk["constrained_columns"] == ["idempotency_key_identity"]


def test_lesson_disposition_snapshot_is_keyed_by_idempotency_identity(
    migrated_db: Database,
) -> None:
    inspector = sa.inspect(migrated_db.engine)
    table = "lesson_disposition_result_snapshots"
    assert inspector.get_pk_constraint(table)["constrained_columns"] == [
        "idempotency_key_identity"
    ]
    checks = " ".join(
        constraint["sqltext"] for constraint in inspector.get_check_constraints(table)
    )
    assert "status IN ('active', 'archived')" in checks
    assert "confidence >= 0" in checks
    assert "confidence <= 1" in checks


def test_lesson_source_ticket_migration_splits_old_positional_related_ids(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/old-lessons.db"
    config = _alembic_config(url)
    command.upgrade(config, "0019")

    product_id = UUID("11111111-1111-4111-8111-111111111111")
    lesson_a = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    lesson_b = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    source_a = UUID("22222222-2222-4222-8222-222222222222")
    source_b = UUID("33333333-3333-4333-8333-333333333333")
    citation_a1 = UUID("44444444-4444-4444-8444-444444444444")
    citation_a2 = UUID("55555555-5555-4555-8555-555555555555")
    citation_b1 = UUID("66666666-6666-4666-8666-666666666666")
    old_related = {
        lesson_a: [source_a, citation_a1, citation_a2],
        lesson_b: [source_b, citation_b1],
    }
    old_related_text = {
        str(lesson_id): [str(ticket_id) for ticket_id in ticket_ids]
        for lesson_id, ticket_ids in old_related.items()
    }
    migrated_at = datetime(2026, 7, 17, 12, tzinfo=UTC)

    engine = sa.create_engine(url)
    metadata = sa.MetaData()
    products = sa.Table("products", metadata, autoload_with=engine)
    lessons = sa.Table("lessons", metadata, autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(
            products.insert().values(
                id=product_id.hex,
                key="ATLAS",
                name="Atlas",
                description="Atlas product",
                vision="Repeatable work",
                status="active",
                goals=[],
                non_goals=[],
                constraints=[],
                created_by_type="human",
                created_by_id="operator",
                created_at=migrated_at,
                updated_at=migrated_at,
            )
        )
        for lesson_id, related_ticket_ids in old_related.items():
            connection.execute(
                lessons.insert().values(
                    id=lesson_id.hex,
                    product_id=product_id.hex,
                    status="active",
                    category="testing",
                    title=f"Legacy lesson {str(lesson_id)[:1]}",
                    problem="Problem",
                    solution="Solution",
                    outcome="Outcome",
                    confidence=0.7,
                    related_ticket_ids=[
                        str(ticket_id) for ticket_id in related_ticket_ids
                    ],
                    related_adr_ids=[],
                    tags=[],
                    created_by_type="agent",
                    created_by_id="lesson-extractor",
                    created_at=migrated_at,
                    updated_at=migrated_at,
                )
            )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT id, source_ticket_id, related_ticket_ids FROM lessons "
                "ORDER BY id"
            )
        ).mappings()
        migrated = {
            _uuid_text(row["id"]): (
                _uuid_text(row["source_ticket_id"]),
                json.loads(row["related_ticket_ids"]),
            )
            for row in rows
        }

    assert migrated == {
        str(lesson_a): (str(source_a), [str(citation_a1), str(citation_a2)]),
        str(lesson_b): (str(source_b), [str(citation_b1)]),
    }
    assert all(
        len(citations) == len(old_related_text[lesson_id]) - 1
        for lesson_id, (_source, citations) in migrated.items()
    )

    command.downgrade(config, "0019")

    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert "source_ticket_id" not in [
            column["name"] for column in inspector.get_columns("lessons")
        ]
        rows = connection.execute(
            sa.text("SELECT id, related_ticket_ids FROM lessons ORDER BY id")
        ).mappings()
        restored = {
            _uuid_text(row["id"]): json.loads(row["related_ticket_ids"]) for row in rows
        }

    assert restored == old_related_text


def test_pm_sync_receipt_migration_preserves_ticket_definition_cursors(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/receipt-upgrade.db"
    config = _alembic_config(url)
    command.upgrade(config, "0021")

    product_id = UUID("11111111-1111-4111-8111-111111111111")
    ticket_id = UUID("22222222-2222-4222-8222-222222222222")
    cursor = datetime(2026, 8, 2, 9, tzinfo=UTC)
    engine = sa.create_engine(url)
    metadata = sa.MetaData()
    products = sa.Table("products", metadata, autoload_with=engine)
    tickets = sa.Table("tickets", metadata, autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(
            products.insert().values(
                id=product_id.hex,
                key="ATLAS",
                name="Atlas",
                description="Atlas product",
                vision="Repeatable work",
                status="active",
                goals=[],
                non_goals=[],
                constraints=[],
                created_by_type="human",
                created_by_id="operator",
                created_at=cursor,
                updated_at=cursor,
            )
        )
        connection.execute(
            tickets.insert().values(
                id=ticket_id.hex,
                product_id=product_id.hex,
                key="ATLAS-245",
                title="Receipt migration",
                objective="Preserve definition cursor.",
                context="Migration parity.",
                status="planned",
                ticket_type="feature",
                risk_level="low",
                priority=1,
                relevant_docs=[],
                tags=[],
                acceptance_criteria=[],
                non_goals=[],
                implementation_notes=[],
                test_requirements=[],
                documentation_requirements=[],
                definition_of_done=[],
                linear_synced_at=cursor,
                review_cycle_count=0,
                source_anchor="docs/atlas/pm-engine-and-linear-sync.md#sync-loop",
                created_by_type="agent",
                created_by_id="pm-engine",
                created_at=cursor,
                updated_at=cursor,
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert "pm_sync_receipts" in inspector.get_table_names()
        stored = connection.execute(
            sa.text("SELECT linear_synced_at FROM tickets WHERE key = 'ATLAS-245'")
        ).scalar_one()

    if isinstance(stored, str):
        assert stored.startswith("2026-08-02 09:00:00")
    else:
        assert stored == cursor


def test_alembic_upgrades_fresh_db_and_matches_metadata(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path}/migrated.db"
    config = _alembic_config(url)
    assert ScriptDirectory.from_config(config).get_heads() == ["0037"]
    command.upgrade(config, "head")

    engine = sa.create_engine(url)
    with engine.connect() as connection:
        assert {
            "admission_leases",
            "admission_eligibility",
            "admission_write_fences",
            "ci_handoff_reconciliations",
            "ci_handoff_write_fences",
            "planned_ci_pending_recoveries",
        } <= set(sa.inspect(connection).get_table_names())
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"migration drifts from ORM metadata: {diff}"


def test_evidence_docs_paths_migration_preserves_historical_rows(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/evidence-docs-paths-upgrade.db"
    config = _alembic_config(url)
    command.upgrade(config, "0034")

    product_id = UUID("11111111-1111-4111-8111-111111111111")
    evidence_id = UUID("22222222-2222-4222-8222-222222222222")
    observed_at = datetime(2026, 8, 20, 9, tzinfo=UTC)
    marker = {
        "_truncated": True,
        "_original_bytes": 70000,
        "_payload_hash": "a" * 64,
        "_source_uri": None,
    }
    engine = sa.create_engine(url)
    metadata = sa.MetaData()
    products = sa.Table("products", metadata, autoload_with=engine)
    evidence = sa.Table("evidence", metadata, autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(
            products.insert().values(
                id=product_id.hex,
                key="ATLAS",
                name="Atlas",
                description="Atlas product",
                vision="Repeatable work",
                status="active",
                goals=[],
                non_goals=[],
                constraints=[],
                created_by_type="human",
                created_by_id="operator",
                created_at=observed_at,
                updated_at=observed_at,
            )
        )
        connection.execute(
            evidence.insert().values(
                id=evidence_id.hex,
                product_id=product_id.hex,
                evidence_type="documentation_update",
                status="passed",
                summary="legacy capped observation",
                commit_sha="c" * 40,
                external_run_id="docs:" + "c" * 40,
                payload_hash="a" * 64,
                raw_payload=marker,
                created_by_type="system",
                created_by_id="github-actions",
                created_at=observed_at,
            )
        )

    command.upgrade(config, "head")

    upgraded = sa.Table("evidence", sa.MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        row = (
            connection.execute(sa.select(upgraded).where(upgraded.c.id == evidence_id))
            .mappings()
            .one()
        )
    assert row["docs_paths"] is None
    assert row["raw_payload"] == marker
    assert _uuid_text(row["id"]) == str(evidence_id)

    command.downgrade(config, "0034")
    with engine.connect() as connection:
        assert "docs_paths" not in {
            column["name"] for column in sa.inspect(connection).get_columns("evidence")
        }


def test_admission_coordination_upgrades_existing_0028_without_metadata_drift(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/admission-coordination-upgrade.db"
    config = _alembic_config(url)
    command.upgrade(config, "0028")

    engine = sa.create_engine(url)
    coordination_tables = {
        "admission_leases",
        "admission_eligibility",
        "admission_write_fences",
    }
    with engine.connect() as connection:
        assert coordination_tables.isdisjoint(sa.inspect(connection).get_table_names())

    command.upgrade(config, "head")

    with engine.connect() as connection:
        assert coordination_tables <= set(sa.inspect(connection).get_table_names())
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"migration drifts from ORM metadata: {diff}"


def test_operator_action_ledger_migration_upgrade_and_downgrade(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/operator-actions.db"
    config = _alembic_config(url)

    command.upgrade(config, "0023")

    engine = sa.create_engine(url)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert "operator_action_keys" in inspector.get_table_names()
        assert "operator_action_receipts" in inspector.get_table_names()
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "operator_action_receipts"
            )
        } == {"operator_action_receipts_outcome_result_code"}
        triggers = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'trigger' AND name LIKE 'operator_action_%'"
                )
            )
        }
    assert {
        "operator_action_keys_no_update",
        "operator_action_keys_no_delete",
        "operator_action_receipts_no_update",
        "operator_action_receipts_no_delete",
    } <= triggers

    command.downgrade(config, "0022")

    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert "pm_sync_receipts" in inspector.get_table_names()
        assert "operator_action_keys" not in inspector.get_table_names()
        assert "operator_action_receipts" not in inspector.get_table_names()
        triggers = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'trigger' AND name LIKE 'operator_action_%'"
                )
            )
        }
    assert triggers == set()


def test_lesson_disposition_snapshot_migration_is_append_only(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/lesson-disposition-snapshots.db"
    config = _alembic_config(url)
    command.upgrade(config, "0025")

    engine = sa.create_engine(url)
    with engine.connect() as connection:
        assert (
            "lesson_disposition_result_snapshots"
            not in sa.inspect(connection).get_table_names()
        )

    command.upgrade(config, "0026")

    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert "lesson_disposition_result_snapshots" in inspector.get_table_names()
        triggers = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE 'lesson_disposition_result_snapshots_%'"
                )
            )
        }
    assert triggers == {
        "lesson_disposition_result_snapshots_no_update",
        "lesson_disposition_result_snapshots_no_delete",
    }

    command.downgrade(config, "0025")

    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert "lesson_disposition_result_snapshots" not in inspector.get_table_names()
        triggers = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE 'lesson_disposition_result_snapshots_%'"
                )
            )
        }
    assert triggers == set()


def test_acceptance_session_migration_pins_identity_and_active_pr(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/acceptance-sessions.db"
    config = _alembic_config(url)
    command.upgrade(config, "0024")

    engine = sa.create_engine(url)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert "acceptance_sessions" in inspector.get_table_names()
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints("acceptance_sessions")
        } == {
            "acceptance_sessions_operator_actor",
            "acceptance_sessions_stale_timestamp",
        }
        indexes = {
            index["name"]: index
            for index in inspector.get_indexes("acceptance_sessions")
        }
        assert indexes["uq_acceptance_sessions_non_terminal_pr"]["unique"] == 1
        triggers = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name = 'acceptance_sessions_pinned_identity'"
                )
            )
        }
        assert triggers == {"acceptance_sessions_pinned_identity"}

    command.downgrade(config, "0023")
    with engine.connect() as connection:
        assert "acceptance_sessions" not in sa.inspect(connection).get_table_names()


def test_delivery_policy_migration_bootstraps_three_without_workflow_change(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/delivery-policy.db"
    config = _alembic_config(url)
    command.upgrade(config, "0023")
    product_id = UUID("11111111-1111-4111-8111-111111111111")
    now = datetime(2026, 8, 2, 14, tzinfo=UTC)
    engine = sa.create_engine(url)
    products = sa.Table("products", sa.MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(
            products.insert().values(
                id=product_id.hex,
                key="ATLAS",
                name="Atlas",
                description="Atlas product",
                vision="Repeatable delivery",
                status="active",
                goals=[],
                non_goals=[],
                constraints=[],
                created_by_type="human",
                created_by_id="operator",
                created_at=now,
                updated_at=now,
            )
        )
    workflow_before = (REPO_ROOT / "WORKFLOW.md").read_bytes()

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT revision, mode, approved_symphony_ceiling, "
                "working_budget, integration_budget, review_budget, "
                "changes_requested_reserve, "
                "risk_lane_limits, component_lane_limits "
                "FROM delivery_admission_policy_revisions "
                "WHERE product_id = :product_id"
            ),
            {"product_id": product_id.hex},
        ).one()
        active_revision = connection.execute(
            sa.text(
                "SELECT revision FROM delivery_admission_policy_active "
                "WHERE product_id = :product_id"
            ),
            {"product_id": product_id.hex},
        ).scalar_one()
        triggers = {
            item[0]
            for item in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE 'delivery_admission_policy_revisions_%'"
                )
            )
        }

    assert row[:7] == (1, "running", 3, 3, 1, 3, 0)
    assert json.loads(row.risk_lane_limits) == []
    assert json.loads(row.component_lane_limits) == []
    assert active_revision == 1
    assert triggers == {
        "delivery_admission_policy_revisions_no_update",
        "delivery_admission_policy_revisions_no_delete",
    }
    assert (REPO_ROOT / "WORKFLOW.md").read_bytes() == workflow_before


def test_ci_pending_capacity_upgrade_preserves_historical_policy_fingerprint(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/ci-pending-policy-upgrade.db"
    config = _alembic_config(url)
    command.upgrade(config, "0030")
    engine = sa.create_engine(url)
    product_id = UUID("11111111-1111-4111-8111-111111111111")
    policy_id = UUID("22222222-2222-4222-8222-222222222222")
    admission_run_id = UUID("33333333-3333-4333-8333-333333333333")
    created_at = datetime(2026, 8, 13, 21, tzinfo=UTC)
    products = sa.Table("products", sa.MetaData(), autoload_with=engine)
    policies = sa.Table(
        "delivery_admission_policy_revisions", sa.MetaData(), autoload_with=engine
    )
    active = sa.Table(
        "delivery_admission_policy_active", sa.MetaData(), autoload_with=engine
    )
    admission_runs = sa.Table("admission_runs", sa.MetaData(), autoload_with=engine)
    legacy_policy_payload = {
        "id": str(policy_id),
        "product_id": str(product_id),
        "revision": 7,
        "mode": "running",
        "approved_symphony_ceiling": 3,
        "working_budget": 2,
        "review_budget": 3,
        "changes_requested_reserve": 1,
        "risk_lane_limits": [],
        "component_lane_limits": [],
    }
    legacy_policy_fingerprint = hashlib.sha256(
        json.dumps(legacy_policy_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    with engine.begin() as connection:
        connection.execute(
            products.insert().values(
                id=product_id.hex,
                key="ATLAS",
                name="Atlas",
                description="Atlas product",
                vision="Repeatable delivery",
                status="active",
                goals=[],
                non_goals=[],
                constraints=[],
                created_by_type="human",
                created_by_id="operator",
                created_at=created_at,
                updated_at=created_at,
            )
        )
        connection.execute(
            policies.insert().values(
                id=policy_id.hex,
                product_id=product_id.hex,
                revision=7,
                mode="running",
                approved_symphony_ceiling=3,
                working_budget=2,
                review_budget=3,
                changes_requested_reserve=1,
                risk_lane_limits=[],
                component_lane_limits=[],
                created_by_type="human",
                created_by_id="operator",
                created_at=created_at,
            )
        )
        connection.execute(
            active.insert().values(product_id=product_id.hex, revision=7)
        )
        connection.execute(
            admission_runs.insert().values(
                id=admission_run_id.hex,
                schema_version="admission-run-v1",
                product_id=product_id.hex,
                policy_id=policy_id.hex,
                policy_revision=7,
                policy_fingerprint=legacy_policy_fingerprint,
                snapshot_fingerprint="a" * 64,
                snapshot_observed_at=created_at,
                evaluated_at=created_at,
                selected_ticket_id=None,
                selected_ticket_key=None,
                decisions=[],
                created_by_type="system",
                created_by_id="atlas.pm.admission",
            )
        )
    with engine.connect() as connection:
        before = connection.execute(sa.select(policies)).mappings().one()
        run_before = connection.execute(sa.select(admission_runs)).mappings().one()

    command.upgrade(config, "head")

    upgraded = sa.Table(
        "delivery_admission_policy_revisions", sa.MetaData(), autoload_with=engine
    )
    upgraded_runs = sa.Table("admission_runs", sa.MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        after = connection.execute(sa.select(upgraded)).mappings().one()
        run_after = connection.execute(sa.select(upgraded_runs)).mappings().one()
        trigger_names = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE 'delivery_admission_policy_revisions_%'"
                )
            )
        }

    assert {key: after[key] for key in before} == dict(before)
    assert dict(run_after) == dict(run_before)
    assert after["integration_budget"] == 1
    assert trigger_names == {
        "delivery_admission_policy_revisions_no_update",
        "delivery_admission_policy_revisions_no_delete",
    }
    with (
        pytest.raises(sa.exc.IntegrityError, match="append-only"),
        engine.begin() as connection,
    ):
        connection.execute(
            upgraded.update()
            .where(upgraded.c.id == policy_id.hex)
            .values(integration_budget=2)
        )

    migrated_db = Database(url)
    historical_policy = DeliveryAdmissionPolicyRepo(migrated_db).get_revision(
        product_id, 7
    )
    historical_run = AdmissionRunRepo(migrated_db).get(admission_run_id)
    assert historical_policy is not None
    assert historical_run is not None
    assert historical_run.policy_fingerprint == legacy_policy_fingerprint
    assert delivery_policy_fingerprint(historical_policy) == legacy_policy_fingerprint


def test_ci_pending_capacity_migration_is_portable_additive_ddl() -> None:
    migration = (
        REPO_ROOT
        / "atlas/storage/migrations/versions/0031_ci_pending_integration_capacity.py"
    ).read_text(encoding="utf-8")

    assert "op.add_column" in migration
    assert "op.batch_alter_table" not in migration
    assert "UPDATE delivery_admission_policy_revisions" not in migration
    assert "0025_delivery_admission_policy" not in migration


def test_ci_pending_capacity_migration_compiles_from_0030_for_postgresql() -> None:
    output = StringIO()
    config = Config(str(REPO_ROOT / "alembic.ini"), output_buffer=output)
    config.set_main_option(
        "script_location", str(REPO_ROOT / "atlas" / "storage" / "migrations")
    )
    config.set_main_option("sqlalchemy.url", "postgresql://atlas:atlas@localhost/atlas")

    command.upgrade(config, "0030:0031", sql=True)

    migration_sql = output.getvalue()
    assert "-- Running upgrade 0030 -> 0031" in migration_sql
    assert (
        "ALTER TABLE delivery_admission_policy_revisions ADD COLUMN "
        "integration_budget INTEGER DEFAULT 1 NOT NULL"
    ) in migration_sql
    assert "delivery_admission_policy_integration_bounds" in migration_sql


def test_ci_handoff_migration_installs_and_removes_append_only_guards(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/ci-handoff.db"
    config = _alembic_config(url)
    command.upgrade(config, "0031")
    engine = sa.create_engine(url)
    with engine.connect() as connection:
        assert (
            "ci_handoff_reconciliations" not in sa.inspect(connection).get_table_names()
        )

    command.upgrade(config, "0032")
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert {
            "ci_handoff_reconciliations",
            "ci_handoff_write_fences",
        } <= set(inspector.get_table_names())
        trigger_names = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE 'ci_handoff_reconciliations_no_%'"
                )
            )
        }
    assert trigger_names == {
        "ci_handoff_reconciliations_no_update",
        "ci_handoff_reconciliations_no_delete",
    }

    command.downgrade(config, "0031")
    with engine.connect() as connection:
        assert {
            "ci_handoff_reconciliations",
            "ci_handoff_write_fences",
        }.isdisjoint(sa.inspect(connection).get_table_names())


def test_ci_handoff_migration_compiles_for_postgresql() -> None:
    output = StringIO()
    config = Config(str(REPO_ROOT / "alembic.ini"), output_buffer=output)
    config.set_main_option(
        "script_location", str(REPO_ROOT / "atlas" / "storage" / "migrations")
    )
    config.set_main_option("sqlalchemy.url", "postgresql://atlas:atlas@localhost/atlas")

    command.upgrade(config, "0031:0032", sql=True)

    migration_sql = output.getvalue()
    assert "-- Running upgrade 0031 -> 0032" in migration_sql
    assert "CREATE TABLE ci_handoff_reconciliations" in migration_sql
    assert "CREATE TRIGGER ci_handoff_reconciliations_append_only" in migration_sql


def test_atlas_280_bootstrap_receipt_migration_installs_and_removes_guards(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/atlas-280-bootstrap.db"
    config = _alembic_config(url)
    command.upgrade(config, "0032")
    engine = sa.create_engine(url)
    with engine.connect() as connection:
        assert (
            "atlas_280_bootstrap_recovery_receipts"
            not in sa.inspect(connection).get_table_names()
        )

    command.upgrade(config, "0033")
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert "atlas_280_bootstrap_recovery_receipts" in inspector.get_table_names()
        trigger_names = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE 'atlas_280_bootstrap_recovery_receipts_no_%'"
                )
            )
        }
    assert trigger_names == {
        "atlas_280_bootstrap_recovery_receipts_no_update",
        "atlas_280_bootstrap_recovery_receipts_no_delete",
    }

    command.downgrade(config, "0032")
    with engine.connect() as connection:
        assert (
            "atlas_280_bootstrap_recovery_receipts"
            not in sa.inspect(connection).get_table_names()
        )


def test_atlas_280_bootstrap_receipt_migration_compiles_for_postgresql() -> None:
    output = StringIO()
    config = Config(str(REPO_ROOT / "alembic.ini"), output_buffer=output)
    config.set_main_option(
        "script_location", str(REPO_ROOT / "atlas" / "storage" / "migrations")
    )
    config.set_main_option("sqlalchemy.url", "postgresql://atlas:atlas@localhost/atlas")

    command.upgrade(config, "0032:0033", sql=True)

    migration_sql = output.getvalue()
    assert "-- Running upgrade 0032 -> 0033" in migration_sql
    assert "CREATE TABLE atlas_280_bootstrap_recovery_receipts" in migration_sql
    assert (
        "CREATE TRIGGER atlas_280_bootstrap_recovery_receipts_append_only"
        in migration_sql
    )


def test_planned_ci_pending_recovery_migration_installs_and_removes_guards(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/planned-ci-pending-recovery.db"
    config = _alembic_config(url)
    command.upgrade(config, "0033")
    engine = sa.create_engine(url)
    with engine.connect() as connection:
        assert (
            "planned_ci_pending_recoveries"
            not in sa.inspect(connection).get_table_names()
        )

    command.upgrade(config, "0034")
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert "planned_ci_pending_recoveries" in inspector.get_table_names()
        trigger_names = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE 'planned_ci_pending_recoveries_no_%'"
                )
            )
        }
    assert trigger_names == {
        "planned_ci_pending_recoveries_no_update",
        "planned_ci_pending_recoveries_no_delete",
    }

    command.downgrade(config, "0033")
    with engine.connect() as connection:
        assert (
            "planned_ci_pending_recoveries"
            not in sa.inspect(connection).get_table_names()
        )


def test_planned_ci_pending_recovery_migration_compiles_for_postgresql() -> None:
    output = StringIO()
    config = Config(str(REPO_ROOT / "alembic.ini"), output_buffer=output)
    config.set_main_option(
        "script_location", str(REPO_ROOT / "atlas" / "storage" / "migrations")
    )
    config.set_main_option("sqlalchemy.url", "postgresql://atlas:atlas@localhost/atlas")

    command.upgrade(config, "0033:0034", sql=True)

    migration_sql = output.getvalue()
    assert "-- Running upgrade 0033 -> 0034" in migration_sql
    assert "CREATE TABLE planned_ci_pending_recoveries" in migration_sql
    assert "CREATE TRIGGER planned_ci_pending_recoveries_append_only" in migration_sql


def test_pm_recovery_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path}/pm-recovery.db"
    config = _alembic_config(url)
    command.upgrade(config, "0035")
    engine = sa.create_engine(url)
    product_id = UUID("11111111-1111-4111-8111-111111111111")
    with engine.begin() as connection:
        assert "pm_recovery_episodes" not in sa.inspect(connection).get_table_names()
        connection.execute(
            sa.text(
                "INSERT INTO products ("
                "id, key, name, description, vision, status, goals, non_goals, "
                "constraints, created_by_type, created_by_id, created_at, updated_at"
                ") VALUES ("
                ":id, 'ATLAS', 'Atlas', 'sentinel', 'sentinel', 'active', "
                "'[]', '[]', '[]', 'human', 'operator', :at, :at)"
            ),
            {"id": product_id.hex, "at": datetime(2026, 8, 31, tzinfo=UTC)},
        )

    command.upgrade(config, "0036")
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert {
            "pm_recovery_sequence_counters",
            "pm_recovery_episodes",
            "pm_blocker_occurrences",
            "pm_blocker_starved_candidates",
        } <= set(inspector.get_table_names())
        assert {
            "ix_pm_recovery_episodes_active_operation",
            "ix_pm_recovery_episodes_active_candidate",
            "ix_pm_recovery_episodes_fairness",
        } == {item["name"] for item in inspector.get_indexes("pm_recovery_episodes")}
        assert {
            "ix_pm_blocker_occurrences_active_operation",
            "ix_pm_blocker_occurrences_active_candidate",
            "ix_pm_blocker_occurrences_episode",
        } == {item["name"] for item in inspector.get_indexes("pm_blocker_occurrences")}

    command.downgrade(config, "0035")
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert {
            "pm_recovery_sequence_counters",
            "pm_recovery_episodes",
            "pm_blocker_occurrences",
            "pm_blocker_starved_candidates",
        }.isdisjoint(inspector.get_table_names())
        assert (
            connection.execute(
                sa.text("SELECT key FROM products WHERE id = :id"),
                {"id": product_id.hex},
            ).scalar_one()
            == "ATLAS"
        )


def test_pm_recovery_migration_compiles_for_postgresql() -> None:
    output = StringIO()
    config = Config(str(REPO_ROOT / "alembic.ini"), output_buffer=output)
    config.set_main_option(
        "script_location", str(REPO_ROOT / "atlas" / "storage" / "migrations")
    )
    config.set_main_option("sqlalchemy.url", "postgresql://atlas:atlas@localhost/atlas")

    command.upgrade(config, "0035:0036", sql=True)

    migration_sql = output.getvalue()
    assert "-- Running upgrade 0035 -> 0036" in migration_sql
    assert "CREATE TABLE pm_recovery_sequence_counters" in migration_sql
    assert "CREATE TABLE pm_recovery_episodes" in migration_sql
    assert "CREATE TABLE pm_blocker_occurrences" in migration_sql
    assert "CREATE TABLE pm_blocker_starved_candidates" in migration_sql
    for required_name in (
        "pm_recovery_sequence_counters_bounds",
        "pm_recovery_episodes_identity_bounds",
        "pm_recovery_episodes_evaluation_fields",
        "pm_recovery_episodes_active_scope",
        "pm_recovery_episodes_replacement_lineage",
        "pm_blocker_occurrences_code",
        "pm_blocker_occurrences_active_or_superseded",
        "pm_blocker_starved_candidates_ordinal_bounds",
        "ix_pm_recovery_episodes_fairness",
        "ix_pm_blocker_occurrences_active_operation",
        "ix_pm_blocker_occurrences_active_candidate",
        "ix_pm_blocker_occurrences_episode",
    ):
        assert required_name in migration_sql

    downgrade_output = StringIO()
    downgrade_config = Config(
        str(REPO_ROOT / "alembic.ini"), output_buffer=downgrade_output
    )
    downgrade_config.set_main_option(
        "script_location", str(REPO_ROOT / "atlas" / "storage" / "migrations")
    )
    downgrade_config.set_main_option(
        "sqlalchemy.url", "postgresql://atlas:atlas@localhost/atlas"
    )
    command.downgrade(downgrade_config, "0036:0035", sql=True)
    downgrade_sql = downgrade_output.getvalue()
    assert downgrade_sql.index("DROP TABLE pm_blocker_starved_candidates") < (
        downgrade_sql.index("DROP TABLE pm_blocker_occurrences")
    )
    assert downgrade_sql.index("DROP TABLE pm_blocker_occurrences") < (
        downgrade_sql.index("DROP TABLE pm_recovery_episodes")
    )
    assert downgrade_sql.index("DROP TABLE pm_recovery_episodes") < (
        downgrade_sql.index("DROP TABLE pm_recovery_sequence_counters")
    )


def test_pm_recovery_blocker_codes_upgrade_downgrade_and_compile(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/pm-recovery-blocker-codes.db"
    config = _alembic_config(url)
    command.upgrade(config, "0036")
    engine = sa.create_engine(url)

    command.upgrade(config, "0037")
    with engine.connect() as connection:
        constraint = next(
            item
            for item in sa.inspect(connection).get_check_constraints(
                "pm_blocker_occurrences"
            )
            if item["name"] == "pm_blocker_occurrences_code"
        )
        assert "ci_evidence_not_yet_complete" in (constraint["sqltext"] or "")
        assert "ci_evidence_ambiguous" in (constraint["sqltext"] or "")
        assert "authority_changed" in (constraint["sqltext"] or "")
        assert "write_fence_unresolved" in (constraint["sqltext"] or "")
        columns = {
            item["name"]
            for item in sa.inspect(connection).get_columns("pm_blocker_occurrences")
        }
        assert "starved_candidates_truncated" in columns

    command.downgrade(config, "0036")
    with engine.connect() as connection:
        constraint = next(
            item
            for item in sa.inspect(connection).get_check_constraints(
                "pm_blocker_occurrences"
            )
            if item["name"] == "pm_blocker_occurrences_code"
        )
        sqltext = constraint["sqltext"] or ""
        assert "publication_not_yet_complete" in sqltext
        assert "ci_evidence_not_yet_complete" not in sqltext
        columns = {
            item["name"]
            for item in sa.inspect(connection).get_columns("pm_blocker_occurrences")
        }
        assert "starved_candidates_truncated" not in columns

    output = StringIO()
    postgres = Config(str(REPO_ROOT / "alembic.ini"), output_buffer=output)
    postgres.set_main_option(
        "script_location", str(REPO_ROOT / "atlas" / "storage" / "migrations")
    )
    postgres.set_main_option(
        "sqlalchemy.url", "postgresql://atlas:atlas@localhost/atlas"
    )
    command.upgrade(postgres, "0036:0037", sql=True)
    migration_sql = output.getvalue()
    assert "-- Running upgrade 0036 -> 0037" in migration_sql
    assert "DROP CONSTRAINT pm_blocker_occurrences_code" in migration_sql
    assert "write_fence_unresolved" in migration_sql
    assert "starved_candidates_truncated" in migration_sql


def test_acceptance_evidence_receipt_outcomes_migrate_without_losing_guards(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/acceptance-evidence-outcomes.db"
    config = _alembic_config(url)
    assert ScriptDirectory.from_config(config).get_heads() == ["0037"]
    command.upgrade(config, "head")

    engine = sa.create_engine(url)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert "admission_runs" in inspector.get_table_names()
        constraints = inspector.get_check_constraints("operator_action_receipts")
        assert len(constraints) == 1
        sqltext = constraints[0]["sqltext"]
        assert sqltext is not None
        assert "external_timeout" in sqltext
        assert "evidence_transport_failed" in sqltext
        assert "evidence_authentication_failed" in sqltext
        assert "evidence_rate_limit_failed" in sqltext
        assert "evidence_malformed_source" in sqltext
        receipt_triggers = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE 'operator_action_receipts_no_%'"
                )
            )
        }
        admission_triggers = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE 'admission_runs_%'"
                )
            )
        }
        assert receipt_triggers == {
            "operator_action_receipts_no_update",
            "operator_action_receipts_no_delete",
        }
        assert admission_triggers == {
            "admission_runs_no_update",
            "admission_runs_no_delete",
        }

    command.downgrade(config, "0027")
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert "admission_runs" in inspector.get_table_names()
        constraints = inspector.get_check_constraints("operator_action_receipts")
        assert len(constraints) == 1
        sqltext = constraints[0]["sqltext"]
        assert sqltext is not None
        assert "evidence_transport_failed" not in sqltext
        receipt_triggers = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE 'operator_action_receipts_no_%'"
                )
            )
        }
        admission_triggers = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE 'admission_runs_%'"
                )
            )
        }
        assert receipt_triggers == {
            "operator_action_receipts_no_update",
            "operator_action_receipts_no_delete",
        }
        assert admission_triggers == {
            "admission_runs_no_update",
            "admission_runs_no_delete",
        }


def test_ddl_compiles_under_postgresql_dialect() -> None:
    # PostgreSQL compatibility is kept honest at compile level on a
    # SQLite-only CI (knowledge-core: no SQLite-only features); a real
    # server round-trip is explicitly not claimed.
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    for table in Base.metadata.sorted_tables:
        statement = sa.schema.CreateTable(table).compile(dialect=dialect)
        rendered = str(statement)
        assert f"CREATE TABLE {table.name}" in rendered
        if any(column.type.__class__.__name__ == "JSONB" for column in table.columns):
            assert "JSONB" in rendered
