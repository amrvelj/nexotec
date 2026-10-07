---
name: drift-audit
description: Weekly check that CLAUDE.md and .claude/rules still match Notion's ADR log and PRDs and the code on main, and that the Kanban board matches git. Read-only on code and spec pages; files its findings as Kanban tickets. Runs as the weekly routine; also usable as /drift-audit.
---
<!-- Deliberately not disable-model-invocation: that flag would also stop the scheduled routine from starting it. -->

# /drift-audit

The repository's instruction files summarise Notion and describe the code. Both move. This
audit finds where the summaries went wrong **before** a session builds on them. It changes no
code, pushes nothing and edits no spec page: every finding becomes a Kanban ticket that Anto
reviews and then runs with `/ticket`, with all the gates.

Work from `origin/main` (`git fetch origin main`, then read with `git show origin/main:<path>`
or a clean checkout at that commit). Today's date and the head you audited go into every
ticket.

## 1. Claims against the code

For `CLAUDE.md` and every file in `.claude/rules/`:

- Every path, symbol, script, test and route named exists at `origin/main`; every "not built",
  "undecided" or "open" statement that cites a ticket (`KAN-<n>`) is checked against that
  ticket's Status and against the code. A ticket now Done, or code that now exists, is a
  finding.
- Every present-tense statement about the code ("X is enforced", "only Y does Z", "the sync
  maps A to B", a field or value list) is checked against the code in full — not only what
  changed since the last audit. A summary can be wrong on the day it is written.
- The maintainer note at the top of each rule file names the commit it was last verified
  against. Diff `origin/main` since that commit for the paths the rule file loads on
  (`git log --stat <sha>..origin/main -- <paths>`) and read the changes for anything the rule
  file states.
- Every rule file's `paths:` globs still match the files its rules govern (`git ls-files`); a
  glob that matches nothing, or code a rule is about that no glob reaches, is a finding.

## 2. Claims against Notion

- Fetch the Target Architecture ADR log. For every ADR the files cite: still active, amended or
  superseded? Any ADR added or amended since the verified date that concerns an area with a rule
  file is a finding ("`sales.md` should state ADR-0xx").
- Check the pages whose rulings the rule files carry for edits since the verified date
  (Dealer Administration, Authentication & Identity, PRD-Customers, PRD-Configurator, the UI/UX
  Specification): read what changed.

## 3. The board against git

- Tickets in **In Review** whose PR has merged (`git log origin/main --oneline --grep "KAN-<n>"`,
  or `gh` when available): list them — Anto closes each with `/ticket KAN-<n> close`.
- Tickets in **In Progress** with no commit mentioning them for seven days: list them.

## 4. The setup itself

`python3 .claude/hooks/nexotec_hooks.py cli selftest` passes; `CLAUDE.md` is at most 200 lines;
every rule file has only `paths` in its frontmatter.

## Output

- **No findings:** create nothing; reply "No drift at <short sha>" with what you checked.
- **Findings:** create ONE ticket on the Nexotec Kanban Board (Status **AI Review**, Type
  Improvement, Severity from the worst finding), titled `Drift audit <YYYY-MM-DD>: <n> findings`,
  in the board's format — What is wrong (each finding with its evidence: file and line in the
  repo, quote from Notion, ticket status), Why it matters, Exit criterion (each rule file
  corrected, its maintainer note updated to the new verified commit), Do not touch (code; spec
  pages), and a Prompt for Claude Code containing the **exact replacement text** for each file.
- A finding that needs Anto's decision rather than a wording fix (Notion and the code disagree
  on a rule) gets its own ticket in the same format, Status **AI Review**.
