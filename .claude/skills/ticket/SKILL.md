---
name: ticket
description: Run one Nexotec Kanban ticket end to end - read it from Notion, plan, build, check, independent review, push, PR, Notion hand-over (CI confirmed at close). Use when Anto asks to work on, implement or fix a KAN ticket (`/ticket KAN-71`), with `land` when he is about to merge its PR (`/ticket KAN-71 land`), and with `close` after he has merged it (`/ticket KAN-71 close`).
argument-hint: "KAN-<n> [land|close]"
---

# /ticket $ARGUMENTS

You are working **$ARGUMENTS** on the Nexotec Kanban board. This skill carries the whole
lifecycle; a ticket's own "DELIVERY" boilerplate is superseded by it. Everything a ticket says
about *what* to build still binds you.

The gates in `.claude/hooks/` enforce the evidence at the end, bound to the exact code tree:
the push needs a passing `scripts/dev/check` for the commit being pushed; the hand-over needs
the PR open for the final commit with no check failed, a reviewer `VERDICT: PASS`, a screenshot
and a Notion update — all for the final code. Green CI is a condition of merging, confirmed at
`/ticket … close` (KAN-619). Do the steps in order and you will never meet a gate by surprise.

Workspace when this skill was invoked:

!`git status -sb | head -5`

!`scripts/dev/gate status 2>&1 | head -14`

If the argument ends in `land`, skip to **Land before the merge**; if it ends in `close`, to
**Close after merge** at the bottom.

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
   Review** → stop and ask Anto: the ticket may not be vetted yet. **Anto Review** → a business
   question waits for him: stop and show him the open question from the callout at the top of
   the page; continue only once the callout carries his answer (then Status → In Progress, and
   build on the branch the callout names, if it names one). **In Review** or **Done** → ask what he wants (a
   follow-up ticket is usually right).
3. Register it: `scripts/dev/gate start KAN-<n> <ticket url>`. From now on, writes to this
   ticket page count as the ticket's hand-over evidence. No Notion write asks Anto (his ruling,
   2026-10-11).
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
- **Overlaps (KAN-684):** run `scripts/dev/overlap <the plan's paths or globs>` (a new migration as
  `alembic/versions/<context>/*`). The plan says what overlaps with which open PR and which lands
  first; a same-chain migration lands after the other PR and is re-parented onto its head. It never
  blocks: two tickets may overlap, they must know.
- **Effort (KAN-621):** sessions run at medium. When the change touches a context boundary, a migration with a backfill, a preserved
  file or a public API contract, or the ticket is Severity High and Type Bug, plan at high: delegate the plan to the `Plan` agent with `effort: high`.
- **Business questions go to Anto; technical decisions are yours, as architect** (CLAUDE.md,
  «How we work»; ADR-126). State the plan, then sort every open point in it:
  - **Technical** — structure, a context boundary, patterns, a migration, tooling, a public API
    contract's shape, refactoring with no visible change, a technical departure from the
    ticket: decide it, write the ADR when it is a decision, say in one plain sentence what you
    decided, and continue. Do not wait for approval. (An edit to a preserved file or the setup
    is a safety rail, not a question: it raises its permission prompt in an attended session
    and is denied in auto, bypassPermissions or dontAsk — finish that part in an attended
    session, KAN-255.)
  - **Business** — what a dealer or user sees or can do, scope or priority (a departure from
    what the ticket asks for included), price, cost or money, legal, tax, compliance or
    contract matters (the auto-i-dat agreement included), retention and privacy, anything that
    changes a ruling of Anto's: ask him — **Asking Anto** below.
  - **Mixed** — split it: decide the technical part, ask only the business part.
- **Asking Anto.** Present → one pop-up choice (AskUserQuestion) per question: the options, your
  recommendation first, and what each option costs. Nobody present — a scheduled run, a cloud
  session, or a question already asked and unanswered → first finish the work that does not
  depend on the answer, then put the question in a callout at the top of the ticket (the
  question, the options, your recommendation, what is done so far, the branch and head), set
  Status → **Anto Review**, and stop that ticket. A run with several tickets goes on with the
  next one (`scripts/dev/gate start` for it); the run's final reply ends with a
  `DECISION NEEDED:` line naming every parked ticket and its question.

## 4. Build

- Honest, small commits. Follow CLAUDE.md and the area rules. Respect "Do not touch".
- A decision the spec does not make is never settled silently in code. Technical: decide it,
  write it as an ADR in the Target Architecture, and tell Anto in one plain sentence. Business:
  **Asking Anto** (step 3) — work that depends on the answer waits; the rest goes on.
- A defect or open question outside this ticket: create a new ticket on the board (Status
  **Backlog**) in the board's format — What is wrong (with paths and line numbers), Why it
  matters, Exit criterion, Do not touch, Prompt for Claude Code. Mention it in the hand-over.

## 5. Verify — in this order, after the last code change

1. Tests: red before green where feasible, on the Postgres lane (plain `pytest`).
2. `scripts/dev/check` until it passes, then commit **everything** (the gate compares trees).
3. **Independent review:** delegate to the `reviewer` agent with: the ticket URL, the exit
   criteria verbatim, the range `origin/main..HEAD`, and what you claim is met and not met. It
   ends with `REVIEWED: <sha>` and its verdict, and the verdict counts only for that commit.
   For any Notion obligation (a ticket criterion that is a Notion write, an ADR, and the
   step 7.2 lines once written), give it your one-sentence statement of each change — page,
   row or anchor line, new text — and tell it to check each with a targeted search
   (`notion-search` with `page_url`) or a row query on the Gap register, never a full fetch
   of the PRD, the Gap Analysis or the Target Architecture; a full fetch only for a statement
   the search cannot confirm (ADR-157).
   This first review is always a **full review**. Fix its findings (or explain in your reply why
   a finding is wrong), **commit, re-run `scripts/dev/check`, then run the reviewer again** - as
   a **re-review** when `scripts/dev/gate review-scope <previously reviewed sha>` allows it
   (KAN-620): give it, on top of the above, the previous report in full, the previously reviewed
   sha and the range `<that sha>..HEAD`. It then rules only on each previous finding, each
   criterion clause not yet met and the scope of the new commits, and ends with
   `RE-REVIEW OF: <previous sha>` before `REVIEWED:`. When `review-scope` answers `FULL REVIEW`
   (the range touches a preserved file, a migration or a `public.py`, changes more than 150
   lines, or the previous sha is not a reviewed ancestor), brief a full review as the first
   time. Only `VERDICT: PASS` on the final commit satisfies the gate; a re-review the hook
   refuses does not.
4. **Screenshot:** run the app in the desktop preview (`.claude/launch.json`: `nexotec-api` +
   `nexotec-frontend`) and screenshot the changed screen — for a fix, the reproduction that no
   longer reproduces; for work without a screen of its own, the nearest visible artefact (the
   migrated record in the running app, say). Preview screenshots are recorded automatically; a
   capture made any other way is saved under `.claude/evidence/` and registered with
   `scripts/dev/gate evidence screenshot <file>`. Only if nothing at all can be shown:
   `scripts/dev/gate no-visual "<why>"`, and say so in the reply. Only one session can run
   the preview (fixed ports 8000/5173).
   **In a cloud session** there is no desktop preview. Start the two `launch.json` commands
   in the background (`scripts/dev/run-api --port 8000` and
   `npm --prefix frontend/apps/dms run dev -- --port 5173 --strictPort`), wait until both
   answer, then `npx playwright screenshot http://localhost:5173/<path> .claude/evidence/<name>.png`
   — the gate records that capture. Stop both servers afterwards.

Any code change after this point invalidates 2–4: redo them.

**Staging** (KAN-686; Anto, 2026-11-13) is read through Render's connector, never changed: a
deploy comes from a merge, a data correction goes through the API as a platform_admin. SQL only
for a check an exit criterion names or to diagnose a failure; **a query result never enters the
repository, a commit message, a PR, an issue or a rule file** — counts and yes/no go to the Notion
ticket, rows only when the ticket asks and they hold no provider data. The rule in full, with the
tables that are counted and never listed: `.claude/README.md`, «Staging».

## 6. Ship

1. `git push -u origin HEAD`, as its own command — Anto approves the push; the gate blocks it
   unless the check passed for exactly this commit, and — after fetching `origin/main` — unless
   HEAD contains `origin/main` (KAN-683: landing is one at a time). Behind: `git merge --no-edit
   origin/main` (never a rebase), `scripts/dev/check`, commit, push. Run `scripts/dev/overlap` again
   first: its answer is the **Overlaps** line of the PR body and of the In Review callout — `none`
   is written, not left out.
2. Open the PR over GitHub's **REST** API — `gh pr create`/`view`/`checks` use GraphQL, which
   cloud sessions refuse (KAN-155). Use the GitHub MCP tools where the session has them, else
   `gh api -X POST 'repos/{owner}/{repo}/pulls' -f title="KAN-<n>: <what changed>" -f head=<branch>
   -f base=main -F body=@<body file>`. The body: the ticket link; what changed; an
   exit-criteria table (met / **NOT met**, with the evidence for each); the verification commands
   and results; what you did not verify; the Overlaps line; follow-up tickets.
3. **Do not wait for CI** (Anto, 2026-11-10; KAN-619). Route B: your `scripts/dev/check` is the
   merge gate (the push hook writes it on the commit as the `nexotec/check` status, KAN-625 - a cloud
   session's GitHub access refuses that write, so there the push carries it as an empty evidence commit
   and the `check-evidence` workflow posts the status, KAN-647: say so in the PR); GitHub's
   `pr-light` runs on every push, drafts included (KAN-653), the full suite at night on `main`. Go on to step 7
   at once — never poll. The hand-over gate stops only on a check that has **already failed**:
   then fix, check → commit → review → push again.

## 7. Hand over

1. Ticket Status → **In Review**. At the top of the page, a callout: `In Review — PR #<n>
   (<branch>), head <sha>`; one line `CI was pending at hand-over — <PR link>` (or what had
   already reported); the Overlaps line; each exit criterion **met** or **NOT met** with its
   evidence; the verification run; the screenshot (attach the file when you have one).
2. **Work is not done when the code merges.** Every time — whether or not the ticket names
   them — write the module PRD's status (what shipped) and the Gap Analysis (citing the new
   head), plus an ADR for any decision taken while building and whatever else the ticket
   names. Write them directly (no Notion write asks Anto, 2026-10-11), and list each spec-page
   change in one plain sentence in the final reply — e.g. "Gap register: add G-175, manual
   configuration accepts unchecked codes, S3." These sentences are what the reviewer and
   `/verify` check, so name the page, the row or line, and what it now says.
   **Never fetch the PRD or the Gap Analysis for this (ADR-157, KAN-622)** — each is a
   targeted write against a fixed anchor or a database row. The two anchors, each one line,
   copied exactly as `old_str` (`new_str` = the anchor, a blank line, then `- <your line>`;
   it lands at the top of the list, newest first):

   ```text
   PRD:  Delivery status — newest first, one line per ticket: date · ticket · what shipped · merged head. Add each line directly below this one; never fetch the page for it (ADR-157).
   Gap:  Heads — newest first, one line per ticket: date · ticket · PR and head (in review) or merge commit · rows touched, or none. Add each line directly below this one; never fetch the page for it (ADR-157).
   ```

   (`PRD:` and `Gap:` are labels here, not part of the anchor.)
   - **PRD status:** one `update_content` on the module's PRD (links in CLAUDE.md) with the PRD
     anchor; your line: `<date> · KAN-<n> · <what shipped, one sentence> · PR #<n>, head <sha> (in review)`.
   - **Gap Analysis, every ticket:** one `update_content` on the Gap Analysis page with the Gap
     anchor; your line: `<date> · KAN-<n> · PR #<n>, head <sha> (in review) · <G-ids touched, or none>`.
   - **Gap register rows** (data source `collection://0c8fe912-888b-40db-862f-9694a07dedae`):
     find a row with `SELECT url, "Gap ID", "Status", "Summary" FROM "collection://0c8fe912-888b-40db-862f-9694a07dedae" WHERE "Gap ID" = 'G-<n>'`
     (a few old ids return two rows; the `Summary` tells them apart). Closing or narrowing a
     gap is one `update_properties` (`Status` Closed or Partly closed, `Cited head`, `Ticket`)
     plus, when it needs words, one `insert_content` at the end of the row's body (what closed
     it, the PR, the head). A new gap is one page created in that data source: `Gap ID` (the
     highest number in use plus one: `SELECT MAX(CAST(SUBSTR("Gap ID", 3) AS INTEGER)) AS n FROM "collection://0c8fe912-888b-40db-862f-9694a07dedae"`),
     `Summary`, `Status` Open, `Severity` S1–S3, `Context`, `Cited head`, `Ticket`; the body
     holds the finding and its evidence.
   - **Only when an anchor or row is not found:** search the page (`notion-search` with
     `page_url`) for the line; fetch the page whole only when the search finds nothing too, and
     say so in the reply. A PRD that lacks the block gets it at the top (`insert_content`,
     position start: `## Status`, the anchor line, your line).
3. Final reply to Anto: PR link, criteria met / not met, verification, screenshot, follow-up
   tickets, and anything waived with its reason.

If the Stop gate blocks you, do exactly what it lists. If something truly cannot be met,
`scripts/dev/gate waive <gate> "<reason>"` asks Anto, and your reply states the reason — if
Notion is unreachable, the reply also carries the exact ticket text (status, callout, criteria
table) for Anto to paste. If you need Anto's decision before you can finish (CI red for a
reason outside the ticket, say), end your reply with a line starting `DECISION NEEDED:` and the
question, and stop: the gate lets that through on the second attempt and keeps the hand-over
open until it is complete. Parking a ticket in **Anto Review** (step 3) uses this path: the
reply carries the `DECISION NEEDED:` line; the gate does not read the ticket's status yet
(KAN-639). Never skip an update silently.

## Land before the merge (`/ticket KAN-<n> land`)

Run when Anto says he is about to merge (KAN-683; ADR-154, «landing is one at a time»). Once
KAN-626's protection is set, `main` requires the branch to be up to date; Route B leaves the
re-check to the session either way.

1. `git fetch origin main`. HEAD already contains `origin/main` and the PR's head is HEAD → say so; done.
2. `git merge --no-edit origin/main` — never a rebase (the gate refuses the force-push it needs).
   A conflict: resolve it by hand, except in generated files, which are regenerated, not merged
   (`schema.d.ts`: `make generate-frontend-types`); a second Alembic head on a chain: re-parent
   this branch's revision onto the other one. Commit the merge.
3. The hook judges the evidence (`scripts/dev/gate status`): a clean merge whose
   `git diff origin/main...HEAD` is byte-identical to before carries the reviewer's PASS and the
   screenshot over, recorded with both shas. A conflict, an edited merge or a different three-dot
   diff carries nothing: review the resolution — a re-review when `scripts/dev/gate review-scope
   <previously reviewed sha>` allows it (for a merge it measures the resolution only and prints the
   `git diff` to give the reviewer as the range), else a full review.
4. `scripts/dev/check` always (the tree is new), then `git push -u origin HEAD`.
5. The ticket's In Review callout gets the new head: `Landed — head <sha> contains main <sha>,
   check passed, evidence carried | re-reviewed`.

## Close after merge (`/ticket KAN-<n> close`)

1. `gh api 'repos/{owner}/{repo}/pulls/<n>' --jq '{merged, merged_at, merge_commit_sha, html_url}'`
   (REST; or the MCP tools). Not merged → stop and say so.
2. **Confirm CI** (KAN-619): `scripts/dev/gate close`. It refuses until the PR is merged, then reads
   the merged PR's checks (its head and its merge commit) and the latest completed nightly run of
   `test.yml` on `main`. A check that **failed** (exit 3, the jobs named): do **not** set Done — file
   a Bug ticket on the board (Status Backlog, the board's format) naming the failing job and the
   run, tell Anto, and stop; the ticket stays In Review. A check still running, a cancelled nightly
   run, or a nightly run that started before the merge is printed and does not hold the close — the
   Done callout then says the nightly run has not covered the merge yet. When `close` prints
   «merged by admin override, <check> was <state>» (a required check of `main`'s protection that
   was not green on the merged head, once KAN-626's protection is set), that line goes on the
   ticket as it stands.
3. **Confirm the deploy** (KAN-686), through Render's connector: `list_workspaces` (one workspace →
   pass its id as `workspaceId`; `select_workspace` is denied), `list_services` → the service named
   `dms-staging` (by name, never a stored id), `list_deploys` with `limit: 1`. Live and its commit is
   the merge commit or a later one on `main` → the ticket gets «deployed live as <short sha> at
   <finishedAt>». Failed (`build_failed`, `update_failed`, …) → name it with the last build and app
   log lines (`list_logs`, `type` build / app, a small `limit`) and file a Bug ticket, as a red
   check does. Still building, or an older commit live → say so; it does not hold the close. To
   learn only whether a deploy ended, `list_events` with `eventTypes: ["deploy_ended"]` is cheaper.
4. Ticket Status → **Done**; change the callout to `Done — merged <date> as <short sha>, PR #<n>`
   and the CI line to what `close` confirmed.
5. Close the loops: PRD status and Gap Analysis against the merged head, and anything else the
   ticket names — written directly, by the cheap path of step 7.2 (no page fetch): a new line
   under each anchor, `<date> · KAN-<n> · merged as <short sha>, PR #<n> · …` (the in-review
   line stays below it), and on each register row the ticket touched, `Cited head` → the merge
   commit. Say what each one changed, as in step 7. Then tell Anto the
   worktree can be removed. A cloud session has no worktree: the session can simply be archived.
