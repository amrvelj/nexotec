# How Claude Code is set up for Nexotec

For Anto and any future developer. Claude does not load this file automatically — it opens it
when it needs to explain or change the setup.

## The pieces and their jobs

| Piece | Loaded | Job |
|---|---|---|
| `CLAUDE.md` | every session | Rules true in every session. No build status. Under 200 lines. |
| `.claude/rules/*.md` | when Claude reads a file matching the rule's `paths` | Rulings for one area (UI, customer, vehicle, sales, …) |
| `.claude/skills/*/SKILL.md` | when invoked (`/ticket`, `/verify`, `/spec-review`, `/architect`, `/drift-audit`) | Workflows |
| `.claude/agents/reviewer.md` | when delegated to | Independent review with fresh context; cannot edit |
| `.claude/hooks/nexotec_hooks.py` | on events, via `settings.json` | Every gate and guard |
| `.claude/settings.json` | every session | Model and effort, permissions, hooks, env |
| `.claude/launch.json` | desktop preview | `nexotec-api` (:8000), `nexotec-frontend` (:5173), `nexotec-prototype` (:8123) |
| `.worktreeinclude` | worktree creation | Copies the gitignored `.env` into new worktrees |
| `scripts/dev/bootstrap` | once per checkout | Own `.venv`, `npm ci`, own test and dev databases, `.env` |
| `scripts/dev/check` | before every push | CI-equivalent lanes; records a pass for the exact tree |
| `scripts/dev/gate` | any time | Gate status, ticket start/close, waivers |

There is no `AGENTS.md` (only Claude Code works in this repository) and no `.mcp.json` (Notion
comes from the claude.ai connector, GitHub goes through `gh`, screenshots through the desktop
preview). Add either only when a concrete need appears.

## The gates

Evidence is recorded against a **git tree hash** — the tree the working copy would produce if
everything were committed — in `<git dir>/nexotec-gates/state.json`, per worktree.

| Gate | Hook | Blocks when |
|---|---|---|
| Push | PreToolUse `Bash` | the working copy differs from HEAD, `scripts/dev/check` has not passed for HEAD's tree, the target is `main` (also `HEAD` while on `main`, `--all`, `--mirror`), or the push is forced |
| Hand-over | Stop | after a push in this session: no PR, CI not green, no reviewer `VERDICT: PASS` for the final tree, no screenshot (or `no-visual` note) for it, or no Notion ticket update after the push |
| Generated files | PreToolUse `Edit|Write` | editing `schema.d.ts` or the built prototype HTML |
| Applied migrations | PreToolUse `Edit|Write` | editing a migration file that exists on `origin/main` |
| Commit | PreToolUse `Bash` | committing on `main`, or `--no-verify` |
| Wrong environment | PreToolUse `Bash` | executing another checkout's `.venv` |
| Notion writes | PreToolUse `mcp__…Notion…` | auto-allows writes to the active ticket and new Kanban tickets; asks for every other page |
| Gate state | PreToolUse `Edit|Write`, `Bash` | writing `nexotec-gates/state.json`, recording a check result other than through `scripts/dev/check`, or waiving other than through `scripts/dev/gate waive` |
| Protected files via the shell | PreToolUse `Bash` | `sed -i`, `cp`, `mv`, `rm`, `tee`, `git mv`/`rm`/`restore`, redirects onto an applied migration or a generated file (denied), or onto a preserved file or the setup itself (asks Anto) |
| Read-only reviewer | PreToolUse `Bash`, `Edit|Write` | the `reviewer` agent writing inside the checkout, committing, fixing, updating snapshots or running `scripts/dev/check` |

Commands are parsed, not pattern-matched: quotes, here-documents (commit messages), `$(…)`,
subshells, pipes, `timeout`/`nohup`/`env` wrappers and `sh -c` are seen through, and a push in
the same command line as a commit, merge or switch is refused. Evidence only counts for the tree
it was recorded on: a reviewer verdict for the commit named on its `REVIEWED:` line,
screenshots from the desktop preview / built-in browser (or a file under `.claude/evidence/`
registered with `scripts/dev/gate evidence screenshot`). Writers hold a lock, so hooks firing
at once never lose each other's events.

A soft reminder (one block per turn) fires when Claude claims "done" with uncommitted changes.
**Pausing for Anto:** after a push, the first stop attempt is always blocked with the list of
what is missing. If Claude needs Anto's decision, it ends its reply with a line starting
`DECISION NEEDED:` and stops again; that second stop is let through, and the missing items show
as `OPEN HAND-OVER` at every session start and in `scripts/dev/gate status` — and gate the next
session in that worktree — until a hand-over completes. `scripts/dev/gate close` (after the
merge) clears it; `close --abandon` drops a ticket without a merge and asks Anto.
**Waivers:** `scripts/dev/gate waive <ci|review|screenshot|notion|all> "<reason>"` asks Anto,
is bound to the current tree and must be stated in Claude's reply. Claude Code releases a stop
gate after eight blocks in a row without progress, so no session can be trapped. A hook that
crashes never blocks (fail-open with a message); a missing hook script exits 0.

`scripts/dev/gate status` prints what the push and the hand-over would say right now.

## One-time machine setup (macOS)

- `brew install gh uv pango`, then `gh auth login`; Docker Desktop running.
- **Pango** is the system library WeasyPrint (PDFs) needs; without it nothing that imports the
  app runs. Homebrew on Apple Silicon keeps it in `/opt/homebrew/lib`, which macOS does not
  search, so `bootstrap`, `check`, the session hook and the preview API set
  `DYLD_FALLBACK_LIBRARY_PATH` themselves. A shell of your own needs the same prefix for app
  commands: `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib:/usr/local/lib:/usr/lib .venv/bin/…`.
- **Port 5432 must be free for the project's Docker database.** Another Postgres on it (for
  example the postgresql.org installer, `/Library/PostgreSQL/<version>`, which starts at boot)
  makes bootstrap stop with "the user dms was rejected". Stop it:
  `sudo launchctl bootout system/<label>` and `sudo launchctl disable system/<label>`, with
  the label from `plutil -extract Label raw /Library/LaunchDaemons/postgresql-<version>.plist`.

## Worktrees and the main checkout

- Code work happens in worktree sessions (desktop app, worktree option). The main checkout stays
  on `main` and clean; a background hook fast-forwards it at session start.
- Each checkout gets its own `.venv` (an editable install points at the checkout it was built
  from — a borrowed `.venv` runs another checkout's code), its own test database
  `dms_test_<checkout>` and, in a worktree, its own dev database `dms_dev_<checkout>`: a copy of
  the main checkout's `dms_platform` (users and data included, so the preview can log in),
  migrated to the branch's heads. `scripts/dev/bootstrap --reset-dev-db` copies it again.
  Every session puts its checkout's `.venv/bin` first on PATH and points `pytest` at its test
  database from the start, so bootstrapping mid-session needs no restart.
- The Docker Compose project is pinned to `nexotec`, so a worktree never starts a second
  Postgres. Only one session can run the desktop preview at a time: CORS and the frontend's API
  base URL are fixed to ports 5173 and 8000 — stop Docker's `app` container
  (`docker compose stop app worker`) before starting the preview. Logged-in screens need a user
  in `dms_platform` and a working local log-in. Today's log-in is WP-4's Zitadel front door,
  which the in-house log-in replaces (D-A-07, `.claude/rules/platform.md`), so no dev tooling
  is built around Zitadel. A worktree's dev database is copied from `dms_platform`, so it has
  whatever users that has.

## Changing the setup

- Hooks run from the checkout the session started in (`$CLAUDE_PROJECT_DIR`), and settings come
  from its `.claude/settings.json`. The hook script is re-read on every call, so an edit to
  `.claude/hooks/` acts at once in the session that made it. Edits to `.claude/hooks/`, `.claude/settings.json`,
  `.claude/agents/` and `scripts/dev/` therefore always ask Anto. Everyone else gets a change
  once it is merged and their checkout is up to date.
- `scripts/dev/check` runs the `config` lane whenever `.claude/`, `CLAUDE.md` or `scripts/dev/`
  change: JSON validity, hook selftest, frontmatter, the CLAUDE.md line budget.
- Rule files carry a maintainer note (an HTML comment, stripped before Claude sees it) naming
  their Notion sources and the commit they were verified against. `/drift-audit` compares them
  weekly with Notion and the code and files tickets for what drifted.
- Model: `claude-opus-5-5` at `effortLevel: high` (Opus 5.5 defaults to `medium`); the reviewer
  runs at `xhigh`. Upgrading is a deliberate one-line change.

## Weekly routine

Code tab → Routines → New routine → **Cloud** → repository `amrvelj/nexotec`, weekly on Monday
07:07, instructions `/drift-audit`, connectors: Notion only.
