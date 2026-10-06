---
name: ticket
description: Run one Nexotec Kanban ticket end to end - read it from Notion, plan, build, check, independent review, push, PR, green CI, Notion hand-over. Use when Anto asks to work on, implement or fix a KAN ticket (`/ticket KAN-71`), and with `close` after he has merged its PR (`/ticket KAN-71 close`).
argument-hint: "KAN-<n> [close]"
---

# /ticket $ARGUMENTS

You are working **$ARGUMENTS** on the Nexotec Kanban board. This skill carries the whole
lifecycle; a ticket's own "DELIVERY" boilerplate is superseded by it. Everything a ticket says
about *what* to build still binds you.

The gates in `.claude/hooks/` enforce the evidence at the end, bound to the exact code tree:
the push needs a passing `scripts/dev/check` for the commit being pushed; the hand-over needs
the PR, green CI, a reviewer `VERDICT: PASS`, a screenshot and a Notion update — all for the
final code. Do the steps in order and you will never meet a gate by surprise.

Workspace when this skill was invoked:

!`git status -sb | head -5`

!`scripts/dev/gate status 2>&1 | head -14`

If the argument ends in `close`, skip to **Close after merge** at the bottom.

## 1. Preconditions

- You must be in a **worktree** session or a **cloud** session (each works on its own copy).
  If the status above says MAIN CHECKOUT, stop and ask Anto to start a worktree or cloud
  session for this ticket.
- If the session status reported that this checkout has no environment of its own, run
  `scripts/dev/bootstrap` first (its tools and test database are picked up in this session).
  A cloud session always needs it: it starts from a fresh copy, and bootstrap also starts that
  machine's Postgres.

## 2. Read the ticket

1. Find it on the board (data source `collection://3cf3e793-34dd-805c-9017-000b5a24915f`,
   property `userDefined:ID` holds the number):
   `SELECT url, Name, Status FROM "collection://3cf3e793-34dd-805c-9017-000b5a24915f" WHERE "userDefined:ID" = <n>`
   via the Notion query tool, then fetch the page and read **all** of it: What is wrong, Root
   cause, Why it matters, Exit criterion, Do not touch, the prompt block.
2. Status gate: **Ready for Development** or **In Progress** → continue. **Backlog** or **AI
   Review** → stop and ask Anto: the ticket may not be vetted yet. **In Review** or **Done** →
   ask what he wants (a follow-up ticket is usually right).
3. Register it: `scripts/dev/gate start KAN-<n> <ticket url>`. From now on, writes to this
   ticket page and new tickets on the board need no click; every other Notion write asks Anto.
4. Name the branch `kan-<n>-<short-slug>`: `git branch -m kan-<n>-<slug>` if the session
   started on a generated name, `git switch -c kan-<n>-<slug>` if it started on `main`.
5. Set the ticket's Status to **In Progress** and comment: branch name, start time.

## 3. Re-verify, then plan

- **Tickets go stale.** Re-check every piece of cited evidence — paths, line numbers,
  symbols, described behaviour — against HEAD before planning. Say what no longer holds.
- Read what the ticket cites (ADRs, PRD sections, sibling tickets), and the ADR or PRD section
  behind any ruling a rule file summarises that your change relies on.
- The plan names: files to change; the test that fails without the change; migrations; API
  contract changes; the Notion updates (below); how you will verify; what the screenshot will
  show.
- **Stop for Anto's approval** — end your turn with the plan — when it touches a context
  boundary, a migration, a preserved file or a public API contract, or when it departs from the
  ticket in any way. Otherwise state the plan briefly and continue.

## 4. Build

- Honest, small commits. Follow CLAUDE.md and the area rules. Respect "Do not touch".
- A decision the spec does not make: ask Anto, or draft it as an ADR for his approval — never
  settle it silently in code.
- A defect or open question outside this ticket: create a new ticket on the board (Status
  **Backlog**) in the board's format — What is wrong (with paths and line numbers), Why it
  matters, Exit criterion, Do not touch, Prompt for Claude Code. Mention it in the hand-over.

## 5. Verify — in this order, after the last code change

1. Tests: red before green where feasible, on the Postgres lane (plain `pytest`).
2. `scripts/dev/check` until it passes, then commit **everything** (the gate compares trees).
3. **Independent review:** delegate to the `reviewer` agent with: the ticket URL, the exit
   criteria verbatim, the range `origin/main..HEAD`, and what you claim is met and not met. It
   ends with `REVIEWED: <sha>` and its verdict, and the verdict counts only for that commit.
   Fix its findings (or explain in your reply why a finding is wrong), **commit, re-run
   `scripts/dev/check`, then re-run the reviewer.** Only `VERDICT: PASS` on the final commit
   satisfies the gate.
4. **Screenshot:** run the app in the desktop preview (`.claude/launch.json`: `nexotec-api` +
   `nexotec-frontend`) and screenshot the changed screen — for a fix, the reproduction that no
   longer reproduces; for work without a screen of its own, the nearest visible artefact (the
   migrated record in the running app, say). Preview screenshots are recorded automatically; a
   capture made any other way is saved under `.claude/evidence/` and registered with
   `scripts/dev/gate evidence screenshot <file>`. Only if nothing at all can be shown:
   `scripts/dev/gate no-visual "<why>"`, and say so in the reply. Only one session can run
   the preview (fixed ports 8000/5173).
   **In a cloud session** there is no desktop preview. Start the two `launch.json` commands
   in the background (`.venv/bin/uvicorn app.main:app --port 8000` and
   `npm --prefix frontend/apps/dms run dev -- --port 5173 --strictPort`), wait until both
   answer, then `npx playwright screenshot http://localhost:5173/<path> .claude/evidence/<name>.png`
   — the gate records that capture. Stop both servers afterwards.

Any code change after this point invalidates 2–4: redo them.

## 6. Ship

1. `git push -u origin HEAD`, as its own command — Anto approves the push; the gate blocks it
   unless the check passed for exactly this commit.
2. Open the PR over GitHub's **REST** API — `gh pr create`/`view`/`checks` use GraphQL, which
   cloud sessions refuse (KAN-155). Use the GitHub MCP tools where the session has them, else
   `gh api -X POST 'repos/{owner}/{repo}/pulls' -f title="KAN-<n>: <what changed>" -f head=<branch>
   -f base=main -F body=@<body file>`. The body: the ticket link; what changed; an
   exit-criteria table (met / **NOT met**, with the evidence for each); the verification commands
   and results; what you did not verify; follow-up tickets.
3. Wait until CI finishes: poll `gh api 'repos/{owner}/{repo}/commits/<head sha>/check-runs'
   --jq '.check_runs[] | [.name, .status, .conclusion] | @tsv'` (or the MCP tools' check runs)
   until every run is `completed`; `scripts/dev/gate status` reads the same REST answer. Red: fix,
   then check → commit → review → push again.

## 7. Hand over

1. Ticket Status → **In Review**. At the top of the page, a callout: `In Review — PR #<n>
   (<branch>), head <sha>`; each exit criterion **met** or **NOT met** with its evidence; the
   verification run; the screenshot (attach the file when you have one).
2. **Work is not done when the code merges.** Every time — whether or not the ticket names
   them — draft the module PRD's status (what shipped) and the Gap Analysis (citing the new
   head), plus an ADR for any decision taken while building and whatever else the ticket
   names. Spec-page writes ask Anto, which is intended. His prompt shows only raw JSON, so
   right before each one say in one plain sentence which page changes and how — e.g. "Gap
   Analysis: add row G-104, manual configuration accepts unchecked codes, severity S3."
3. Final reply to Anto: PR link, criteria met / not met, verification, screenshot, follow-up
   tickets, and anything waived with its reason.

If the Stop gate blocks you, do exactly what it lists. If something truly cannot be met,
`scripts/dev/gate waive <gate> "<reason>"` asks Anto, and your reply states the reason — if
Notion is unreachable, the reply also carries the exact ticket text (status, callout, criteria
table) for Anto to paste. If you need Anto's decision before you can finish (CI red for a
reason outside the ticket, say), end your reply with a line starting `DECISION NEEDED:` and the
question, and stop: the gate lets that through on the second attempt and keeps the hand-over
open until it is complete. Never skip an update silently.

## Close after merge (`/ticket KAN-<n> close`)

1. `gh api 'repos/{owner}/{repo}/pulls/<n>' --jq '{merged, merged_at, merge_commit_sha, html_url}'`
   (REST; or the MCP tools). Not merged → stop and say so.
2. Ticket Status → **Done**; change the callout to `Done — merged <date> as <short sha>, PR #<n>`.
3. Close the loops: PRD status and Gap Analysis against the merged head, and anything else the
   ticket names — spec-page writes ask Anto; say what each one changes first, as in step 7.
4. `scripts/dev/gate close` (it refuses until the PR is merged), then tell Anto the worktree can
   be removed. A cloud session has no worktree: the session can simply be archived.
