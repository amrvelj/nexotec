# How Claude Code is set up for Nexotec

For Anto and any future developer. Claude does not load this file automatically — it opens it
when it needs to explain or change the setup.

The repository `amrvelj/nexotec` is **public for now** (Anto, 2026-11-13) and returns to private;
until then nothing the identAPI agreement's §5 or §12 covers enters it (`.claude/rules/vehicle-gateway.md`).

## What a rule file may contain (KAN-614)

`CLAUDE.md` loads in every session and a rule file whenever a matching file is read — in the
builder and again in every reviewer pass — so each line costs on every ticket. A rule file holds
**only what a session cannot learn from the code or from a failing test**:

- **A ruling a test enforces:** one line, naming the test. The test is the rule; the line is the
  pointer.
- **A ruling stated in Notion:** the ruling in a sentence and its ADR or PRD reference — never the
  reasoning, which lives at the reference.
- **An invariant a session would otherwise get wrong** (a trap the code does not show, an open
  question, "not built" where the code looks finished): kept, as short as it can be said.
- **Never:** ticket history («KAN-n because …»), what the code already says (routes, columns,
  function signatures), or a second copy of a PRD section.

Budget: a rule file at most **12,000 bytes**, `CLAUDE.md` at most **16,000 bytes** (and 200 lines).
`scripts/dev/check`'s `config` lane runs `scripts/rule_budget.py check`, which prints every file's
size and fails over budget, with no allowlist: until KAN-615 has cut them, a file already over on
`main` may only shrink. A file that outgrows its budget is split by sub-area, each part with globs
for its own files only. `scripts/rule_budget.py scenarios` shows which rule files three typical
tickets load, before and after a change.

## The pieces and their jobs

| Piece | Loaded | Job |
|---|---|---|
| `CLAUDE.md` | every session | Rules true in every session. No build status. At most 200 lines. |
| `.claude/rules/*.md` | when Claude reads a file matching the rule's `paths` | Rulings for one area (UI, customer, vehicle, sales, …) |
| `.claude/skills/*/SKILL.md` | when invoked (`/ticket`, `/verify`, `/spec-review`, `/architect`, `/drift-audit`) | Workflows |
| `.claude/agents/reviewer.md` | when delegated to | Independent review with fresh context; cannot edit |
| `.claude/hooks/nexotec_hooks.py` | on events, via `settings.json` | Every gate and guard |
| `.claude/settings.json` | every session | Model and effort, permissions, hooks, env |
| `.claude/launch.json` | desktop preview | `nexotec-api` (:8000), `nexotec-frontend` (:5173), `nexotec-prototype` (:8123) |
| `.worktreeinclude` | worktree creation | Copies the gitignored `.env` into new worktrees |
| `scripts/dev/bootstrap` | once per checkout, and in every cloud session | Own `.venv`, `npm ci`, own test and dev databases, `.env` |
| `scripts/dev/cloud-postgres` | cloud sessions, from bootstrap and check | Starts the cloud machine's own Postgres 16 with the `dms` user |
| `scripts/dev/run-api` | desktop preview (`nexotec-api`) | Starts this checkout's uvicorn with the macOS library path set |
| `scripts/dev/check` | before every push | CI-equivalent lanes (pytest on one Postgres database per worker, KAN-627); records a pass for the exact tree, with the lanes left out and the test counts |
| `scripts/dev/gate` | any time | Gate status, ticket start/close, waivers |
| `scripts/dev/api-type` | any time | One schema or API path from the generated `schema.d.ts`, which is never read whole (KAN-618) |
| `scripts/dev/overlap` | `/ticket` step 3 (the plan's paths or globs) and step 6 (the branch's changes) | Which open PRs change the same files, with their ticket and head, and which add a revision to the same Alembic chain; REST only, file names only (never another PR's diff), never blocks, exit 0; `--json` for callers, `--selftest` in the `config` lane (KAN-684) |
| `scripts/dev/usage-report` | on a Mac with the sessions' transcripts (not in a cloud session) | Claude usage per session from the local transcripts: tokens per agent, where the input went, the largest tool results; counts and file names only, no network (KAN-613) |

There is no `AGENTS.md` (only Claude Code works in this repository) and no `.mcp.json` (Notion
and Render come from claude.ai connectors, GitHub goes through `gh api` (REST only — KAN-155), screenshots through the desktop
preview). Add either only when a concrete need appears.

## The gates

Evidence is recorded against a **git tree hash** — the tree the working copy would produce if
everything were committed — in `<git dir>/nexotec-gates/state.json`, per worktree.

| Gate | Hook | Blocks when |
|---|---|---|
| Push | PreToolUse `Bash` | the working copy differs from HEAD, `scripts/dev/check` has not passed for HEAD's tree, the target is `main` (also `HEAD` while on `main`, `--all`, `--mirror`), the push is forced, or — after `git fetch origin main` — the pushed commit does not contain `origin/main` (KAN-683: landing is one at a time; the refusal names `git merge --no-edit origin/main` and `scripts/dev/check`). A failed fetch never blocks: the gate says so and judges by the last known `origin/main` |
| Evidence across a merge of main (KAN-683) | PostToolUse `Bash` (`git merge`/`pull`/`commit`), Stop, `gate status` | never blocks. On a merge commit at HEAD: when git reproduces it without conflict (`git merge-tree --write-tree`), its tree is git's own result, and `git diff origin/main...HEAD` is byte-identical to the first parent's, the reviewer's PASS and the screenshot of the first parent's tree are recorded again for the merge's tree (`evidence-carried`, both shas). Otherwise nothing carries (`evidence-not-carried`, the reason) and the resolution is reviewed under `review-scope`'s rules. The check never carries: the tree is new. `/ticket KAN-n land` is the step that uses it |
| Hand-over | Stop | after a push in this session: no open PR for HEAD, a CI check that has already **failed** (a pending one does not hold it — KAN-619), no reviewer `VERDICT: PASS` for the final tree, no screenshot (or `no-visual` note) for it, or no Notion ticket update after the push. `gate close` then refuses while the merged PR's checks or the latest nightly run on `main` are red, and prints «merged by admin override, <check> was <state>» for a check `main`'s protection requires that was not green on the merged head — `/ticket … close` puts it on the ticket (KAN-626) |
| Check status on the PR (KAN-625) | PostToolUse `Bash` (the push), Stop (again, while GitHub was not reached) | never blocks. After a push lands, the hook posts the commit status `nexotec/check` = `success` on each pushed commit whose tree equals the recorded pass, saying which lanes ran («frontend only» / «all lanes»), the tree and the test counts; no pass for that tree, no status. |
| | | Only the hook writes it: a typed `gh`/`curl` call to a commit-status or check-run write path is refused, whatever its context (a rail against the plain mistake, not a boundary: a path hidden in a variable or script is not seen). A refusal by GitHub is recorded (`gate status`: `PR status`) and reported, never worked around. |
| Check evidence commit (KAN-647) | PreToolUse `Bash` (a push the gates let through) | where the status write is refused (a cloud session, or a checkout whose last write GitHub refused with 403), the hook first adds one empty commit on HEAD's tree whose message carries the recorded pass (tree, lanes, lanes not run, counts) under the `Nexotec-Check-Evidence:` trailer; only for a tree with a recorded pass and a push of the current branch the gates let through, never two in a row. The tree does not change, so the review, the screenshot and the push gate still hold. A push that does not land has the commit dropped again; a push whose permission prompt is declined leaves it, unpushed, for the next push. `.github/workflows/check-evidence.yml` (`statuses: write`; not for forks) posts `nexotec/check` = `success` on a head that has one parent, is empty against it and names its own tree, saying it came from an evidence commit; `error` with the reason for a marked head that fails that; nothing otherwise. A typed `git commit`/`commit-tree` carrying the trailer is refused: inline, in a file, a template, a `<` redirect, piped in with the marker written in the command, or reused (`--amend`, `-C`/`-c`). The squash merge folds the evidence commit's message into the squash commit's (the repository's setting), trailer included. Not signed, and judged by the PR's own copy of the rules (ADR-129). |
| | | Proven from a cloud session on 2026-11-13 (KAN-647, criterion 4): a real PR whose head is the hook's evidence commit got `nexotec/check` = `success` from `github-actions`, and a marked head naming a wrong tree got `error`. |
| Generated files | PreToolUse `Edit|Write`, `Read`, `Bash` | editing `schema.d.ts` or the built prototype HTML; reading `schema.d.ts`, `openapi.json` or `frontend/package-lock.json` whole (KAN-618) — a `Read` without a `limit` of at most 200 lines, and `cat`, `less`, `sed` without a bounded range or `head`/`tail` over 200 lines, are refused with a pointer to `scripts/dev/api-type` and `grep -n`. Not a `Read(...)` deny rule: Claude Code applies one to `grep` on the same path too |
| Applied migrations | PreToolUse `Edit|Write` | editing a migration file that exists on `origin/main` |
| Preserved files and the setup | PreToolUse `Edit|Write`, `Bash` | a write to a CLAUDE.md preserve-verbatim file, `.claude/hooks/`, `.claude/agents/`, `.claude/settings.json` or `scripts/dev/`: asks Anto in an attended permission mode (`default`, `acceptEdits`, `plan`); **denied** in every other mode (`auto`, `bypassPermissions`, `dontAsk`, an unknown or missing mode), with a message to ask Anto in chat and stop — an unattended session never changes these files (KAN-255) |
| Commit | PreToolUse `Bash` | committing on `main`, or `--no-verify` |
| Wrong environment | PreToolUse `Bash` | executing another checkout's `.venv` |
| Notion writes | PreToolUse `mcp__…Notion…` | allows every Notion write without a prompt (Anto, 2026-10-11), except from the read-only `reviewer`, which is denied, and except `notion-update-page` with `replace_content` on a page that is neither the active ticket nor one this session created (the post-tool hook records what `notion-create-pages` returns): it rewrites the whole page from what the session fetched, so a parallel session's edit would silently disappear — the refusal names `update_content` and `insert_content` (KAN-685); a write to the active ticket is recorded as hand-over evidence. Claude says in one plain sentence which page changed and how (CLAUDE.md). Reads need no prompt either: `settings.json` allows the whole Notion server under both names it appears as (`mcp__claude_ai_Notion` on the Mac, `mcp__Notion` in a cloud session) |
| Render (KAN-686) | `settings.json` permissions, PreToolUse `mcp__…Render…` | the twelve read tools (`list_workspaces`, `list_services`, `get_service`, `list_deploys`, `get_deploy`, `list_events`, `list_logs`, `list_log_label_values`, `get_metrics`, `list_postgres_instances`, `get_postgres`, `query_render_postgres`) are allowed and every other tool of the connector is denied by name, under `mcp__Render` (cloud) and `mcp__claude_ai_Render` (the Mac — by the Notion connector's pattern, not yet read off a Mac session); the hook denies every non-read under any server name. The recorded tool list is `RENDER_TOOLS` in the hook; the selftest fails when one is in neither list. Rules: «Staging» below |
| Gate state | PreToolUse `Edit|Write`, `Bash` | writing `nexotec-gates/state.json`, recording a check result other than through `scripts/dev/check`, or waiving other than through `scripts/dev/gate waive` |
| Protected files via the shell | PreToolUse `Bash` | `sed -i`, `cp`, `mv`, `rm`, `tee`, `git mv`/`rm`/`restore`, redirects onto an applied migration or a generated file (denied), or onto a preserved file or the setup itself (asks Anto in an attended mode, denied otherwise — the row above) |
| Read-only reviewer | PreToolUse `Bash`, `Edit|Write`, `mcp__…Notion…` | the `reviewer` agent writing inside the checkout or to Notion, committing, fixing, updating snapshots or running `scripts/dev/check` |

Commands are parsed, not pattern-matched: quotes, here-documents (commit messages), `$(…)`,
subshells, pipes, `timeout`/`nohup`/`env` wrappers and `sh -c` are seen through, and a push in
the same command line as a commit, merge or switch is refused. Evidence only counts for the tree
it was recorded on: a reviewer verdict for the commit named on its `REVIEWED:` line (a
re-review's, which also names `RE-REVIEW OF: <previous sha>`, only when that commit is an ancestor
with a recorded review and the range between them changes at most 150 lines and no preserved file,
migration or `public.py` — otherwise the verdict is recorded as refused; `scripts/dev/gate
review-scope <sha>` answers beforehand, KAN-620),
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
  search, so `bootstrap`, `check`, the session hook and `scripts/dev/run-api` (what the
  preview's `nexotec-api` starts) set `DYLD_FALLBACK_LIBRARY_PATH` themselves. It cannot go
  in `launch.json`'s `env`: the desktop app starts preview servers through a launcher signed
  with the hardened runtime, which strips every `DYLD_*` variable before uvicorn sees it
  (KAN-109). A shell of your own needs the same prefix for app commands:
  `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib:/usr/local/lib:/usr/lib .venv/bin/…`, or
  `scripts/dev/run-api` for the API.
- **Port 5432 must be free for the project's Docker database.** Another Postgres on it (for
  example the postgresql.org installer, `/Library/PostgreSQL/<version>`, which starts at boot)
  makes bootstrap stop with "the user dms was rejected". Stop it:
  `sudo launchctl bootout system/<label>` and `sudo launchctl disable system/<label>`, with
  the label from `plutil -extract Label raw /Library/LaunchDaemons/postgresql-<version>.plist`.

## Worktrees and the main checkout

- Code work happens in worktree sessions (desktop app, worktree option) or in cloud sessions
  (next section). The main checkout stays
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
  in `dms_platform` and a working local log-in: Nexotec's own (D-A-07, KAN-196) — seed one with
  `DMS_SEED_DEMO_PASSWORD=… python scripts/seed_staging_demo.py`, or read an invitation link out
  of the API log (the `logging` mail sender). A worktree's dev database is copied from
  `dms_platform`, so it has whatever users that has.

## Cloud sessions

Start one from the desktop app or claude.ai/code with the environment set to **Cloud**, or from
the phone's Code tab: repository `amrvelj/nexotec`, branch `main`, then `/ticket KAN-n`. The
session clones the repository from GitHub, so CLAUDE.md, the rules, the skills, the reviewer
and the hooks apply exactly as on the Mac. It works on its own fresh copy, as isolated as a
worktree, so ticket work runs there; afterwards there is no worktree to remove — archive the
session. Several can run at once, each on its own machine, with the Mac closed.

- `scripts/dev/bootstrap` runs in every cloud session, because each starts fresh. There is no
  Docker daemon there: bootstrap first starts the machine's own PostgreSQL 16 and creates the
  `dms` user (`scripts/dev/cloud-postgres`), and `scripts/dev/check` starts it again when a
  paused machine has lost it.
- There is no desktop preview. The screenshot step starts the API and the frontend in the
  background and captures with `npx playwright screenshot`, which the gate records.
- The Drive folder (UI prototype, auto-i-dat PDFs) is out of reach: tickets that need it run on
  the Mac.
- Notion comes from the same claude.ai connector, but its server is named `mcp__Notion` here
  (`mcp__claude_ai_Notion` on the Mac) and its tools are deferred: an agent with a `tools:` list
  needs both names and `ToolSearch` to reach it (KAN-695). `gh` is pre-installed and authenticated
  through the cloud's GitHub proxy, which refuses GraphQL (`gh pr view`, `gh pr checks`,
  `gh pr create`): the gates and `/ticket` use `gh api` REST calls, which work here and on the
  Mac. The weekly `/drift-audit` routine is a cloud session too.
- Staging is read through Render's connector (`mcp__Render` here), under the rules in «Staging»
  below: the reads are allowed without a prompt, every tool that changes Render is denied.

## Staging (KAN-686; Anto, 2026-11-13; ADR-155)

Sessions may read staging — the service `dms-staging`, its deploys, events, logs and metrics, and
the staging database through `query_render_postgres` (Render runs every query in a read-only
transaction) — and never change it.

- **What SQL is for:** a check a ticket's exit criterion names, or diagnosing a failure. Nothing else.
- **A query result never enters the repository, a commit message, a PR, an issue or a rule file**
  (the repository and its PRs are public for now). Counts and yes/no go to the Notion ticket; rows
  only when the ticket asks for them and they hold no provider data.
- **Provider-derived tables are counted, never listed:** the per-tenant catalogue mirror
  (`vehicle_colour_cache`, `vehicle_tyre_spec_cache`, `vehicle_image_ref`, `vehicle_variant_option`,
  `vehicle_variant_option_equipment_feature`, `vehicle_variant_option_relation`,
  `vehicle_variant_option_provider_code`), the detail and lookup caches (`vehicle_variant_detail_state`,
  `vehicle_plate_lookup_cache`), the call log (`integration_call_log`, `integration_call_payload`) and
  the provider captions (`integration_provider_caption`, `integration_provider_caption_fetch`).
- **A session never changes staging:** a deploy is triggered by a merge; a data correction goes
  through the API as a platform_admin. No agent login on staging (§3, §8: personal logins).
- **Resolve by name, at run time:** `list_workspaces` (one workspace → pass its id as `workspaceId`;
  `select_workspace` is denied), the service named `dms-staging`, the Postgres instance from
  `list_postgres_instances`. No id goes into the repository.
- **Cost:** `list_deploys` returns each commit's whole message (a squash merge runs to thousands of
  words): ask for `limit: 1`; `list_events` with `deploy_ended` when the status alone is enough.
- The application's rule is unchanged: provider data is never exposed through an MCP server
  (`.claude/rules/vehicle-gateway.md`, `test_no_mcp_server.py`). This ruling is about the tooling.

## Changing the setup

- Hooks run from the checkout the session started in (`$CLAUDE_PROJECT_DIR`), and settings come
  from its `.claude/settings.json`. The hook script is re-read on every call, so an edit to
  `.claude/hooks/` acts at once in the session that made it. Edits to `.claude/hooks/`, `.claude/settings.json`,
  `.claude/agents/` and `scripts/dev/` therefore always ask Anto, and only an attended session
  (`default`, `acceptEdits` or `plan` mode) can make them: in `auto`, `bypassPermissions` or
  `dontAsk` the hook denies the edit (KAN-255). Everyone else gets a change
  once it is merged and their checkout is up to date.
- `scripts/dev/check` runs the `config` lane whenever `.claude/`, `CLAUDE.md` or `scripts/dev/`
  change: JSON validity, hook selftest, frontmatter, the CLAUDE.md line budget, the rule budget
  (`scripts/rule_budget.py check`, top of this file).
- Rule files carry a maintainer note (an HTML comment, stripped before Claude sees it) naming
  their Notion sources and the commit they were verified against. `/drift-audit` compares them
  weekly with Notion and the code and files tickets for what drifted.
- Model: `claude-opus-5-5` at `effortLevel: medium` (KAN-621). `/architect` and `/spec-review`
  run at `high` (`effort:` in their frontmatter, for the turn that invokes them), and `/ticket`
  plans at `high` through the `Plan` agent for the cases it lists; the reviewer runs at `high`
  (KAN-620). `CLAUDE_CODE_EFFORT_LEVEL`, when set, overrides all of these. Upgrading is a
  deliberate one-line change.

## Weekly routine

Code tab → Routines → New routine → **Cloud** → repository `amrvelj/nexotec`, weekly on Monday
07:07, instructions `/drift-audit`, connectors: Notion only.
