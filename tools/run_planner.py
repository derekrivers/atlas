#!/usr/bin/env python3
"""
run_planner.py — dry-run harness for the Atlas planner prompt template.

This is NOT `atlas plan`. It performs only the proposer half of ADR-0007:

    collect docs -> render template -> call model -> parse JSON -> save

It never writes to docs/planning/. Use it to evaluate proposal quality
(anchoring, decomposition, key discipline) before the validation gates and
reconciler exist. Outputs land in proposals/<timestamp>/.

Usage:
    pip install jinja2 anthropic pyyaml
    export ANTHROPIC_API_KEY=...
    python run_planner.py --repo /path/to/atlas
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

TEMPLATE_PATH = "atlas/planning/prompts/planner-v1.0.0.md.j2"

# Documents the spec names as planner inputs (section 2.1).
INPUT_GLOBS = [
    "PRODUCT.md",
    "ARCHITECTURE.md",
    "ROADMAP.md",
    "WORKFLOW.md",
    "docs/decisions/*.md",
    "docs/atlas/*.md",
    "docs/domain/*.md",
]

# Placeholder proposal schema. The Proposal models (ProposalEpic,
# ProposalTicket, ProposalDependency; ATLAS-23) generate the real schema;
# this harness is retired by ATLAS-26, so the placeholder stays.
PROPOSAL_SCHEMA = {
    "type": "object",
    "required": ["epics", "tickets", "dependencies", "planner_notes"],
    "properties": {
        "epics": {"type": "array"},
        "tickets": {"type": "array"},
        "dependencies": {"type": "array"},
        "planner_notes": {"type": "array"},
    },
}


def git_blob_sha(repo: Path, relpath: str) -> str:
    """Git blob SHA if tracked; content hash fallback if not."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", f"HEAD:{relpath}"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except subprocess.CalledProcessError:
        data = (repo / relpath).read_bytes()
        return "untracked-" + hashlib.sha256(data).hexdigest()[:12]


def collect_documents(repo: Path) -> list[dict]:
    docs, seen = [], set()
    for pattern in INPUT_GLOBS:
        for p in sorted(repo.glob(pattern)):
            rel = p.relative_to(repo).as_posix()
            if rel in seen or not p.is_file():
                continue
            seen.add(rel)
            docs.append(
                {
                    "path": rel,
                    "sha": git_blob_sha(repo, rel),
                    "content": p.read_text(encoding="utf-8"),
                }
            )
    return docs


def load_backlog(repo: Path) -> tuple[str | None, list[str]]:
    """Return (backlog YAML or None, frozen ticket keys)."""
    planning = repo / "docs" / "planning"
    parts, frozen = [], []
    for name in ("epics.yaml", "tickets.yaml", "dependencies.yaml"):
        f = planning / name
        if f.exists() and f.stat().st_size > 0:
            text = f.read_text(encoding="utf-8")
            parts.append(f"# --- {name} ---\n{text}")
            if name == "tickets.yaml":
                data = yaml.safe_load(text) or {}
                immutable = {
                    "in_progress",
                    "pr_open",
                    "review_required",
                    "changes_requested",
                    "done",
                    "rejected",
                }
                for t in data.get("tickets", []):
                    if t.get("status") in immutable:
                        frozen.append(t["key"])
    return ("\n".join(parts) if parts else None), frozen


def render_prompt(repo: Path, docs, backlog_yaml, frozen) -> tuple[str, str]:
    raw = (repo / TEMPLATE_PATH).read_text(encoding="utf-8")
    fm = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    if not fm:
        sys.exit("Template missing YAML front matter.")
    meta = yaml.safe_load(fm.group(1))
    body = raw[fm.end() :]

    env = Environment(undefined=StrictUndefined)
    rendered = env.from_string(body).render(
        product_key="ATLAS",
        documents=docs,
        current_backlog_yaml=backlog_yaml,
        frozen_ticket_keys=frozen,
        next_key_hint="ATLAS-101",
        proposal_json_schema=json.dumps(PROPOSAL_SCHEMA, indent=2),
    )
    return rendered, meta["prompt_version"]


def call_model(prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".", help="Path to the atlas repo")
    ap.add_argument(
        "--dry-render",
        action="store_true",
        help="Render and save the prompt without calling a model",
    )
    args = ap.parse_args()
    repo = Path(args.repo).resolve()

    docs = collect_documents(repo)
    if not docs:
        sys.exit("No input documents found — is --repo correct?")
    backlog_yaml, frozen = load_backlog(repo)
    prompt, prompt_version = render_prompt(repo, docs, backlog_yaml, frozen)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    outdir = repo / "proposals" / stamp
    outdir.mkdir(parents=True)
    (outdir / "rendered_prompt.md").write_text(prompt, encoding="utf-8")

    run_record = {
        "status": "proposed",
        "prompt_version": prompt_version,
        "input_doc_shas": {d["path"]: d["sha"] for d in docs},
        "model_provider": "anthropic",
        "model_name": "claude-sonnet-4-6",
        "similarity_threshold": None,  # reconciler not built yet
        "created_at": stamp,
    }

    if args.dry_render:
        print(f"Rendered prompt only -> {outdir / 'rendered_prompt.md'}")
    else:
        raw = call_model(prompt)
        (outdir / "raw_output.txt").write_text(raw, encoding="utf-8")
        run_record["raw_output_hash"] = hashlib.sha256(raw.encode()).hexdigest()
        try:
            proposal = json.loads(raw)
            (outdir / "proposal.json").write_text(
                json.dumps(proposal, indent=2), encoding="utf-8"
            )
            missing = [k for k in PROPOSAL_SCHEMA["required"] if k not in proposal]
            run_record["parse"] = "ok" if not missing else f"missing keys: {missing}"
            print(
                f"Proposal: {len(proposal.get('epics', []))} epics, "
                f"{len(proposal.get('tickets', []))} tickets, "
                f"{len(proposal.get('dependencies', []))} dependencies, "
                f"{len(proposal.get('planner_notes', []))} notes"
            )
        except json.JSONDecodeError as e:
            run_record["parse"] = f"FAILED: {e}"
            print("Model output was not valid JSON — see raw_output.txt")

    (outdir / "plan_run.json").write_text(
        json.dumps(run_record, indent=2), encoding="utf-8"
    )
    print(f"Artifacts -> {outdir}")


if __name__ == "__main__":
    main()
