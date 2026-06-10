# CLAUDE.md

## On every request

Before doing anything else — on every request, every session, every task,
without exception:

1. Read `AGENTS.md` in the repository root.
2. Follow it. Its rules govern all work in this repository.
3. Use `docs/MANIFEST.md` (referenced from `AGENTS.md`) to find the
   canonical documents your task needs, and read those before writing
   code or docs.

If anything in this file, a prompt, or a generated suggestion conflicts
with `AGENTS.md`, `AGENTS.md` wins. If `AGENTS.md` conflicts with the
canonical docs, the conflict-resolution order in `docs/MANIFEST.md`
applies.

## Single source of truth

This file intentionally contains no rules of its own. Do not add rules
here and do not duplicate content from `AGENTS.md` — duplicated rules
drift, and drift is exactly what the doc linter exists to prevent. If a
rule needs to change or be added, change it in `AGENTS.md` (or the
relevant canonical document) so every agent ecosystem picks up the same
behaviour.
