---
name: verify
description: Independent audit of delivered Nexotec work - a PR, a Kanban ticket, a work package or a commit range - against its specification. Trusts no commit message, runs everything, rules on every exit criterion clause by clause. Use as /verify PR-118, /verify KAN-71, /verify WP-5 or /verify <base>..<head>.
argument-hint: "<PR-n | KAN-n | WP-n | base..head>"
disable-model-invocation: true
---

# /verify $ARGUMENTS

An audit, not a build. Nothing gets fixed here, no PR is opened, and no code is written except
throwaway scripts needed to check a claim. The audit itself runs in the `reviewer` agent, which
has fresh context and cannot edit files, so the work is never graded by whoever produced it.

## 1. Resolve the target (you, in this session)

- `PR-<n>`: `gh pr view <n> --json title,body,headRefName,baseRefOid,headRefOid,mergeCommit,url`.
  The ticket is the `KAN-<n>` in its title or body.
- `KAN-<n>`: fetch the ticket from the board (see `/ticket` for the query); find its PR with
  `gh pr list --state all --search "KAN-<n>"`.
- `WP-<n>`: the work package page in the Build Sequence (Notion, linked in CLAUDE.md); the
  range is the merged head it names against the previous verified head.
- `<base>..<head>`: as given.

Collect: the specification (ticket or WP page — its prompt block counts as spec), the exit
criterion, the constraints and "do not touch" list, the ADRs and PRD sections it names, the git
range, and the "Notion updates when this closes" obligations.

## 2. Put the code where it can run

The audit must run the exact code under review. If this checkout is not at the target head,
create a separate worktree (`git worktree add .claude/worktrees/verify-<target> <head>`),
run `scripts/dev/bootstrap` in it, and point the reviewer there: every command it runs is
`cd <that worktree> && .venv/bin/<tool> …`, because a bare `pytest` or `python` resolves to
this session's environment and imports this session's code.

## 3. Delegate the audit to the `reviewer` agent

Give it everything from step 1 and the directory from step 2, plus this brief:

- Read all sources before writing a single finding. Notion is authoritative where it and
  CLAUDE.md or the rules differ.
- Commit messages are claims. Claims like "backfills all rows", "every lane is green", "zero
  behaviour change" or "discussed with Anto" are checked against the code and a run, or reported
  as unverifiable.
- **Run everything you can:** `ruff check app tests`, `python -m mypy app`, `lint-imports`,
  `pytest` on Postgres, `alembic upgrade heads` against an empty Postgres database, and the
  frontend chain (oxlint, `tsc -b`, `vitest run`, `vite build`). Any migration that backfills
  data: run it for real and query the result (rows, nulls, values equal to their source). Any
  test that gates the exit criterion: run it, watch it pass, and prove it cannot skip silently.
- Check what the staging build serves (`https://dms-staging-tb9w.onrender.com/openapi.json`) so
  "merged" and "live" are not confused.
- Rule on: each outstanding item (quote the spec, show the code, done / partial / not done);
  the exit criterion clause by clause (say which clauses were verified by execution); every
  constraint (held / violated / not exercised — for preserve-verbatim files, whether behaviour
  changed, separately from whether the file changed); anything real but outside the spec
  (inconsistent payloads, inert fields, breaking required fields, lanes that gate nothing); and
  each Notion delivery obligation against the live page.

## 4. Report

Write the reviewer's report as a markdown file in the Nexotec Drive folder (path in CLAUDE.md),
named `<target>-verification-<YYYY-MM-DD>.md`, structured:

1. **Verdict** — one paragraph: closed or not, and exactly what stands between it and closed.
2. **What I actually ran** — lane → result, separating verified from assumed.
3. **Item by item** — verdict and required fixes per item.
4. **Constraints** — constraint → held / violated / not exercised.
5. **Still open** — a numbered list Anto can work straight through, including every Notion edit
   still outstanding.

Rank findings by whether they block the exit criterion; label hygiene points as such. Then post
the verdict paragraph and the report's file name as a comment on the ticket (the write asks
Anto), and give Anto the same summary in chat. If everything passes, say so plainly and stop.
