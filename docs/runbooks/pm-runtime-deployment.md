# Runbook: Managed PM Runtime Deployment

This is the canonical operator procedure for deploying one accepted Atlas
release into `atlas-pm-sync.service`. It owns deployment and runtime activation;
it does not alter the acceptance protocol, automate production operations, or
grant delivery authority.

The supported topology is one Linux host, one managed PM process and one
file-backed SQLite operational store. ATLAS-068M uses a non-blocking OS advisory
lock on the existing SQLite database file. It does not establish distributed
ownership and it deliberately refuses in-memory SQLite, SQLite URI/non-file
stores, PostgreSQL and other network database URLs.

Every command in this runbook is an operator action. Stop on a failed or
ambiguous check. Do not compensate by running `atlas pm sync --once`, editing
the database, moving a Linear card, changing delivery policy, or restoring an
old snapshot after the post-first-write boundary.

## 1. Authority and immutable identities

Deployment is separate from acceptance and merge. The target is a generic exact
accepted `<target-sha>` on `origin/main` containing both the desired application
changes and every required migration. It is never inferred from a mutable branch
name and is not the earlier ATLAS-281 merge alone.

Use operator-local shell variables only for the deployment session. Do not put
credentials in shell history, the receipt, or retained command output.

```bash
pm_target_sha=<exact-accepted-main-sha>
pm_previous_sha=<exact-current-runtime-sha>
pm_release_root=/root/atlas-runtime/pm-sync
pm_target_dir="$pm_release_root/$pm_target_sha"
pm_database_path=/root/atlas/.atlas/atlas.db
pm_database_url="sqlite:///$pm_database_path"
pm_unit=atlas-pm-sync.service
```

Fetch and prove the target before touching production:

```bash
git -C /root/atlas fetch origin main
git -C /root/atlas cat-file -e "$pm_target_sha^{commit}"
git -C /root/atlas merge-base --is-ancestor "$pm_target_sha" origin/main
test "$(git -C /root/atlas rev-parse "$pm_target_sha")" = "$pm_target_sha"
git -C /root/atlas cat-file -e \
  "$pm_target_sha:atlas/storage/migrations/versions/0034_planned_ci_pending_recovery.py"
git -C /root/atlas cat-file -e \
  "$pm_target_sha:atlas/pm/writer_ownership.py"
```

Establish and record, without changing them:

- the canonical database path from the service configuration and its sanitized
  identity `sha256:<SHA-256 of the absolute real path>`;
- `systemctl show` values for `FragmentPath`, `DropInPaths`, `LoadState`,
  `ActiveState`, `SubState`, `MainPID`, `NRestarts`, `ExecMainStartTimestamp` and
  `WorkingDirectory`;
- the current process working directory, executable and target-specific command
  prefix from `/proc/<MainPID>`; inspect the command locally but do not retain a
  secret-bearing command line;
- the current pinned release SHA;
- the current database revision;
- the exact target Alembic head;
- the active delivery-policy revision, canonical fingerprint and mode;
- a bounded pre-deployment snapshot of Atlas ticket statuses and Linear state
  IDs/fingerprint for later canary comparison.

Require exactly one active policy and `mode=paused`. This runbook does not create
or activate a policy revision. A running or indeterminate policy stops the
deployment before downtime.

Read the effective unit digest without storing its contents in the receipt:

```bash
systemctl cat "$pm_unit" | sha256sum
```

If the unit contains inline secrets, do not copy or paste its contents. Preserve
the exact fragment/drop-in files for pre-first-write rollback in a root-only
operator backup, but never copy an environment file or its values into the
deployment receipt.

## 2. Prepare the target release before downtime

The release directory must not already exist. Resolve `origin` from
`/root/atlas`, but never echo, retain or pass through a credential-bearing URL.
The supported origin is canonical credential-free GitHub HTTPS. This bounded
check emits only that safe canonical form and fails closed for userinfo,
passwords/tokens, ports, query/fragment data, non-GitHub hosts, non-HTTPS
schemes, malformed paths or a failed Git read:

```bash
pm_origin_url=$(python3 - <<'PY'
import re
import subprocess
from urllib.parse import urlsplit

result = subprocess.run(
    ["git", "-C", "/root/atlas", "remote", "get-url", "origin"],
    check=False,
    capture_output=True,
    text=True,
)
if result.returncode:
    raise SystemExit("cannot resolve the authoritative Atlas origin")
raw = result.stdout.strip()
parsed = urlsplit(raw)
if (
    parsed.scheme != "https"
    or parsed.netloc != "github.com"
    or parsed.query
    or parsed.fragment
):
    raise SystemExit("origin is not credential-free canonical GitHub HTTPS")
parts = parsed.path.removeprefix("/").removesuffix(".git").split("/")
if len(parts) != 2 or not all(
    re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts
):
    raise SystemExit("origin repository identity is malformed")
print(f"https://github.com/{parts[0]}/{parts[1]}.git")
PY
)
test ! -e "$pm_target_dir"
mkdir -p "$pm_release_root"
git clone --no-checkout "$pm_origin_url" "$pm_target_dir"
git -C "$pm_target_dir" fetch --no-tags origin main
test "$(git -C "$pm_target_dir" rev-parse origin/main)" = \
  "$(git -C /root/atlas rev-parse origin/main)"
git -C "$pm_target_dir" cat-file -e "$pm_target_sha^{commit}"
git -C "$pm_target_dir" checkout --detach "$pm_target_sha"
test ! -e "$pm_target_dir/.git/objects/info/alternates"
(cd "$pm_target_dir" && uv sync --locked)
test "$(git -C "$pm_target_dir" rev-parse HEAD)" = "$pm_target_sha"
test -z "$(git -C "$pm_target_dir" status --porcelain --untracked-files=no)"
```

The independent target repository must contain the exact fetched upstream-main
identity before checkout and must not use a shared-object alternates file whose
long-lived validity depends on `/root/atlas`. Require the directory name, Git
HEAD and expected SHA to agree. Require `uv.lock` to be committed at that head
and the PM command launcher to be `$pm_target_dir/.venv/bin/atlas`. Do not pull,
switch or update `/root/atlas` local `main`; clone a mutable ticket branch; or
point the service at a branch or unpinned worktree. Apply operator-owned
permissions that prevent the service identity from modifying tracked release
content; do not modify the release after it receives live authority.

Calculate and record the single exact target Alembic head before downtime:

```bash
pm_target_head=$(cd "$pm_target_dir" && .venv/bin/alembic heads | awk '{print $1}')
test -n "$pm_target_head"
test "$(cd "$pm_target_dir" && .venv/bin/alembic heads | wc -l)" -eq 1
```

## 3. Stop and prove PM quiescence

Capture the old `MainPID`, `NRestarts`, release identity and unit digest, then:

```bash
systemctl stop "$pm_unit"
test "$(systemctl is-active "$pm_unit")" = inactive
test "$(systemctl show --property MainPID --value "$pm_unit")" = 0
test -z "$(pgrep -f '[a]tlas pm sync' || true)"
```

Prove the existing database is a regular file and that writer ownership can be
acquired and released without running a PM tick. Run the check from the target
release because it contains the approved ownership implementation:

```bash
test -f "$pm_database_path"
(cd "$pm_target_dir" && ATLAS_DATABASE_URL="$pm_database_url" \
  .venv/bin/python - <<'PY'
from atlas.pm.writer_ownership import pm_writer_ownership
from atlas.storage import Database

with pm_writer_ownership(Database()):
    print("PM writer ownership released")
PY
)
```

Do not proceed unless the service is inactive, no PM command remains, and this
nonblocking acquisition succeeds. A lock refusal is evidence of another writer,
not permission to kill an unidentified process or delete a file.

While a PM process owns the database, unlinking, replacing, restoring or
recreating the database pathname is unsupported and prohibited. `flock` follows
the opened inode; pathname replacement would create another filesystem object
and defeat path-level operational assumptions. Backup, migration, restore and
replacement occur only after this quiescence proof. ATLAS-068M does not attempt
filesystem tamper protection.

## 4. Back up SQLite while quiesced

Create a target-specific root-only backup directory outside release content.
Use SQLite's backup operation, not `cp` of the database file:

```bash
pm_window=$(date -u +%Y%m%dT%H%M%SZ)
pm_backup_dir="$pm_release_root/backups/$pm_window-$pm_target_sha"
pm_backup_path="$pm_backup_dir/atlas-pre-migration.db"
install -d -m 0700 "$pm_backup_dir"
sqlite3 "$pm_database_path" ".backup '$pm_backup_path'"
test -s "$pm_backup_path"
stat --format='%s' "$pm_backup_path"
sha256sum "$pm_backup_path"
sqlite3 -readonly "$pm_backup_path" 'PRAGMA quick_check;'
```

Require `quick_check` to return exactly `ok`. Capture the pre-migration revision
from both the canonical store and backup and require them to agree. Record the
backup path, byte size and lowercase SHA-256. Do not start or otherwise grant
the target runtime authority yet.

## 5. Migrate with the target release

Read and record the current revision immediately before migration. Run Alembic
only from the target release against the canonical URL:

```bash
(cd "$pm_target_dir" && ATLAS_DATABASE_URL="$pm_database_url" \
  .venv/bin/alembic current)
(cd "$pm_target_dir" && ATLAS_DATABASE_URL="$pm_database_url" \
  .venv/bin/alembic upgrade head)
pm_after_head=$(cd "$pm_target_dir" && ATLAS_DATABASE_URL="$pm_database_url" \
  .venv/bin/alembic current | awk '{print $1}')
test "$pm_after_head" = "$pm_target_head"
```

Any missing, multiple or unequal revision stops activation. Repeat `PRAGMA
quick_check` against the migrated canonical store and require `ok`. Migration
success does not itself make the release active.

## 6. Point the service at the pinned release

Change only the intended service release identity. Preserve all cadence flags,
restart policy, user, environment-file references, hardening and unrelated unit
configuration. Do not copy secrets into the unit or receipt.

The effective `WorkingDirectory` must be `$pm_target_dir`. The executable must
come from `$pm_target_dir/.venv/`; preserve the existing `atlas pm sync`
arguments exactly. Use the established operator-owned systemd edit boundary,
then:

```bash
systemctl daemon-reload
systemctl show --property FragmentPath --property DropInPaths \
  --property WorkingDirectory "$pm_unit"
systemctl cat "$pm_unit" | sha256sum
```

Record the after digest and require the expected fragment/drop-in identities.
Review the effective unit locally and fail if anything except the pinned release
location changed.

## 7. Start and prove the managed process

Record `NRestarts`, start the service, and require one stable process:

```bash
systemctl start "$pm_unit"
test "$(systemctl is-active "$pm_unit")" = active
pm_new_pid=$(systemctl show --property MainPID --value "$pm_unit")
test "$pm_new_pid" -gt 0
readlink -f "/proc/$pm_new_pid/cwd"
readlink -f "/proc/$pm_new_pid/exe"
systemctl show --property NRestarts --property ExecMainStartTimestamp "$pm_unit"
```

Require the process working directory and command/virtual-environment identity
to prove the exact target release, the effective command to be the intended
recurring `atlas pm sync`, `/proc/<MainPID>/exe` to be the interpreter resolved
by that target virtual environment, and `NRestarts` to remain at its expected
value. Re-read the database revision with the target release and require exact
target head.

Starting the target gives it an opportunity to write. From this point, treat
the deployment as post-first-write unless bounded evidence proves the process
never entered a PM tick and no target-runtime receipt or state mutation exists.

## 8. Observe the natural-cadence canary

Do not run `atlas pm sync --once`. Observe `atlas-pm-sync.service` on its normal
scheduler cadence. Starting the recurring scheduler and its ordinary first tick
is a natural managed invocation; a separate writer is not a canary.

Starting from the pre-deployment receipt high-water, require exactly one newer
`pm_sync_receipts` row attributable to the managed PM engine. Record its bounded
ID, start/finish times, result and counters. A successful canary result is one of
the repository-defined successful PM receipt classifications; a missing,
partial, malformed, cancelled or failed receipt is not a pass.

Corroborate all of the following:

- the service remains active with the same non-zero `MainPID`;
- target SHA, process working directory and executable remain exact;
- `NRestarts` did not unexpectedly increase;
- database revision still equals the target head;
- active policy revision/fingerprint is unchanged and remains paused;
- admission/promotion counters are zero;
- the bounded pre/post Atlas and Linear workflow snapshots show no unexpected
  workflow-state movement;
- no second writer refusal or ordinary tick failure appeared in the bounded
  service journal window.

Use receipt IDs, fingerprints and bounded counters in evidence. Do not retain
raw Linear/GitHub payloads, environment values, unit contents or arbitrary
journal output.

## 9. Deployment receipt

Write exactly one root-readable JSON receipt atomically beneath:

```text
/root/atlas/.atlas/pm-runtime-deployments/<UTC-window>-<target-sha>.json
```

The schema identifier is `pm-runtime-deployment-receipt-v1`. The bounded record
contains:

```text
deployment_id
window_started_at, window_finished_at
previous_release_sha, target_release_sha
database_identity_fingerprint
database_revision_before, database_revision_after
backup_path, backup_size_bytes, backup_sha256
service_unit
service_unit_digest_before, service_unit_digest_after
old_main_pid, new_main_pid
old_process_release_sha, new_process_release_sha
restart_count_before, restart_count_after
natural_canary_receipt_id, natural_canary_result
natural_canary_started_at, natural_canary_finished_at
natural_canary_counters
workflow_fingerprint_before, workflow_fingerprint_after
unexpected_workflow_movement_count
policy_revision, policy_fingerprint, policy_mode
target_live_write_observed
outcome, failure_stage
```

Use UTC timestamps, lowercase SHA-256, integer counters and bounded enum-like
outcomes. The database identity is a digest of its canonical absolute real path,
not a raw URL. Never include credentials, tokens, environment values, unit
contents, raw command lines, secret-bearing paths, arbitrary logs or provider
payloads.

The receipt is observational evidence only. It is not a database authority
record, admission authority, policy authority, ticket-state authority, rollback
authority, migration state or permission to continue. No application table is
created for it.

Only after every preceding check passes and the receipt records a successful
outcome may `<target-sha>` be called the managed PM runtime.

## 10. Rollback and incident boundary

### Before the target can have written

If failure occurs after migration but before the target starts—or bounded proof
establishes that the started target never entered a tick and made no write—keep
PM quiesced. Restore the pre-migration backup with SQLite's restore operation,
not a raw file copy; restore the previous exact unit definition and pinned
release; reload systemd; require the restored database revision to equal the
previous release's expected head; start the previous service; and repeat its
process and natural-cadence checks. Record the failed deployment outcome and
rollback observations in the same bounded receipt.

The backup and prior unit/release are inputs to an operator decision; the
receipt itself grants no rollback authority.

### Once the target may have written

If the target has entered a tick, emitted a receipt, changed local state, called
Linear/GitHub, or write absence cannot be proved:

1. stop `atlas-pm-sync.service` gracefully;
2. prove process quiescence and preserve the database, receipt, unit and bounded
   journal evidence;
3. do not restore an older database snapshot, downgrade Alembic, or restart the
   retired release against the migrated store;
4. classify a deployment incident and require an explicit operator recovery
   decision.

Data written after migration must never be silently discarded for rollback
convenience.
