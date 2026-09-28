---
name: spec-review
description: Adversarial review of the Nexotec specification in Notion - Target Architecture and ADR log, Build Sequence, Gap Analysis, Risk Register and every PRD - for contradictions, gaps and unrealistic requirements. Produces findings with paste-ready replacement text; never edits Notion. Use as /spec-review or /spec-review <area>.
argument-hint: "[area, e.g. sales or dealer-administration]"
disable-model-invocation: true
---

# /spec-review $ARGUMENTS

Stress-test the specification before more is built on it. Not validation: assume it contains
real mistakes that Anto is too close to see. Scope: the whole specification, or only the area
named in the arguments (plus everything that area depends on).

## Sources — read all of them before writing a single finding

- Target Architecture and its ADR log, Build Sequence, Gap Analysis, Risk Register (links in
  CLAUDE.md).
- The Requirements tree (https://app.notion.com/p/3b53e79334dd800195e9cfe6c851bd06): discover
  every child page instead of relying on a list, including Dealer Administration,
  Authentication & Identity and Integrations & API Credentials. Treat any page you find as in
  scope.
- `CLAUDE.md` and `.claude/rules/` in the repository — the working summaries. Notion is
  authoritative where they differ; every disagreement is a finding.
- The code, wherever the spec asserts something about what shipped. The Gap Analysis may itself
  be stale; if it is, that is a finding.

Page through large pages; never summarise from a partial read. **Build a complete inventory
first** — every ADR by number with a one-line decision and its status (active / amended /
superseded), every PRD with its scope in one line — and show it before any finding. If the
inventory is wrong, the review is worthless.

## Stance

Adversarial. Quote the spec verbatim before criticising it; if you cannot quote it, drop the
finding. Never soften a finding and never invent one. No praise anywhere except, at most, five
bullets at the very end naming what should stay exactly as it is. A sound page gets "no
findings".

## Passes, in this order

1. **Cross-PRD consistency (highest value).** For every fact in more than one PRD, exactly one
   context owns it and the others hold a foreign ID. Check every cross-context interaction for a
   named past-tense event with an owner and a consumer; list interactions that are implied
   synchronous calls; check no user-facing flow needs more than two synchronous hops.
2. **Internal consistency.** Superseded decisions leaking into live pages; work packages that
   assume what an ADR rules out; requirements that break a non-negotiable rule (CLAUDE.md).
3. **ADR quality.** A decision rather than a restated requirement; consequences including the bad
   ones; rejected alternatives with reasons; a revisit trigger. Flag ADRs to delete or defer.
4. **PRD quality.** Testable requirements; complete state machines (every state, transition and
   who may make it); unhappy paths; what is out of v1; permissions reconcilable with Dealer
   Administration.
5. **Swiss completeness.** VAT (ADR-057 and the fiktiver Vorsteuerabzug), gapless numbering and
   period lock, plates and Wechselschild, group vs dealership visibility, provider licence
   partitioning, revDSG retention and deletion, i18n of reference data, archival duty — and the
   unglamorous parts: incumbent-DMS migration, backup and restore, onboarding, support access,
   auto-i-dat outages.
6. **Realism and sequencing.** Correct-in-principle requirements with no v1 path (name the honest
   v1 substitute); over-engineering; untestable exit criteria; wrong dependencies.
7. **Risk Register.** Missing risks, undeserved ones; each needs an owner, a trigger and a
   concrete mitigation.

## Output

A markdown file in the Nexotec Drive folder (path in CLAUDE.md): `spec-review-<YYYY-MM-DD>.md`,
with (1) the inventory, (2) cross-PRD contract findings, (3) all other findings, (4) decisions
Anto must make — split into *decide now, blocks building* and *can defer, and until when* —
with the options and your recommendation, (5) at most five things to keep exactly as they are.

Each finding:

```
### F-NN — <one-line title>
Severity: blocker | serious | minor
Page / ADR: <where>
Quote: "<verbatim from the spec>"
Problem: <2–4 sentences, concrete>
Why it matters: <what breaks, in the product or the build>
Proposed replacement: <paste-ready Notion text — the actual wording>
```

Sort by severity, then page. Do not edit Notion: Anto applies the text. If a whole page needs
rewriting rather than patching, say so early. Stop and ask if the review depends on something
nobody has told you.
