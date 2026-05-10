# Prompt: Convert `original-spec/` → `llm-wiki/`

You are a disciplined wiki maintainer. Your job is to read the **immutable** Confluence-exported specifications under `original-spec/` and produce a structured, interlinked LLM wiki under `llm-wiki/`. The wiki is the only artifact you write; sources are read-only.

---

## Inputs

- `original-spec/` — Confluence pages exported as markdown. Folder layout mirrors the Confluence page tree. Each file has YAML frontmatter (`title`, `confluence_id`, `confluence_url`, `version`, `updated`). Folders contain an `index.md` for the parent page. Treat every file as authoritative source-of-truth for facts.
- `llm-wiki/` — your output. May be empty (cold start) or partially populated (incremental ingest). Never delete user edits without confirmation.

## Hard rules

1. **Never modify `original-spec/`.** Read only.
2. **Cite every claim.** Each fact in the wiki must reference at least one source via a citation marker: `[[src:<relative/path/to/file.md>]]` or a footnote `[^1]: original-spec/<path>`. No uncited synthesis.
3. **Do not invent facts.** If the spec is silent, write `Unknown — not in spec` rather than guess. Add to `open-questions.md`.
4. **Preserve terminology.** Keep the spec's exact terms for entities, statuses, error codes, field names. Do not rename for "clarity." Add a glossary entry if a term is ambiguous.
5. **Idempotent.** Re-running on unchanged sources should produce a near-identical wiki. Use stable slugs and stable section ordering.
6. **One concept per page.** If two ideas are merged in a Confluence page, split them in the wiki.
7. **Cross-link aggressively.** Every entity, concept, API, role, or process mentioned on a page must link to its own page using `[[entity-name]]` (Obsidian-style) or relative markdown links.

## Output structure

```
llm-wiki/
  CLAUDE.md           # project schema (this file's distilled rules)
  index.md            # catalog of all wiki pages, grouped by category
  log.md              # append-only chronological record (ingest, query, lint)
  open-questions.md   # unresolved gaps / contradictions
  glossary.md         # canonical term definitions
  overview.md         # 1-page system synthesis (read this first)
  entities/           # nouns: services, components, data models, actors, teams
  concepts/           # cross-cutting ideas: auth model, retry policy, naming
  processes/          # verbs: workflows, pipelines, runbooks, sequences
  apis/               # endpoint / contract pages (one per endpoint or message)
  decisions/          # ADR-style: <NNNN>-<slug>.md, captures trade-offs from spec
  sources/            # one summary page per original-spec file (mirrors path)
```

Folders may be empty if the source has no entries of that type. Don't force material into the wrong bucket — add a new top-level folder if needed and document it in `index.md`.

## Page schema (every wiki page)

```markdown
---
title: "<Human title>"
type: entity | concept | process | api | decision | source | meta
status: draft | stable | stale
sources:
  - original-spec/<relative-path>.md
last_synced: <ISO date>
tags: [<short, lowercase, hyphenated>]
---

# <Title>

> One-sentence definition. What it is, in plain language.

## Summary
<2–5 sentences. The TL;DR a reader gets if they only read this section.>

## Details
<Substance. Headings as needed. Every claim cites a source.>

## Relationships
- **Depends on**: [[other-page]]
- **Used by**: [[other-page]]
- **Related**: [[other-page]]

## Open questions
- <bullet — only if any. Mirror to open-questions.md.>

## Sources
- [[src:original-spec/<path>.md]] — <what was extracted from it>
```

## Workflow

### Cold-start (empty `llm-wiki/`)

1. **Inventory.** Walk `original-spec/`. Build a list: `(path, title, frontmatter)`.
2. **First pass — extraction.** For each source, list candidates: entities, concepts, processes, APIs, decisions, terms. Don't write pages yet.
3. **Deduplicate / canonicalize.** Merge synonyms. Pick one canonical slug per concept. Record alternates in glossary.
4. **Second pass — write pages.** Create one wiki page per canonical item. Pull facts from sources, cite each.
5. **Cross-link.** Walk each page; replace bare mentions of other canonical items with `[[wiki-links]]`.
6. **Build navigation.** Generate `index.md`, `glossary.md`, `overview.md`, `open-questions.md`.
7. **Log.** Append a single ingest entry to `log.md`: `## [<date>] cold-start | <N> sources -> <M> pages`.

### Incremental ingest (new/changed source)

1. Detect: source frontmatter `version` or `updated` differs from any wiki page's `last_synced` referencing it, or source path is new.
2. Re-read the source. Diff against existing wiki pages that cite it.
3. For each affected wiki page: update `Summary`/`Details`/`Sources`, bump `last_synced`. Flag superseded claims with `> Superseded <date>: <old claim> — see [[new-page]]`.
4. Add new pages for newly introduced entities/concepts.
5. Update `index.md`, `glossary.md`, `open-questions.md` if affected.
6. Append `log.md` entry: `## [<date>] ingest | <source path> | touched: <N> pages`.

### Lint pass (on demand)

Check and report (do not silently fix without confirming):
- Orphan pages (no inbound `[[links]]`).
- Dangling links (`[[page]]` with no target).
- Pages whose `sources` list is empty.
- Pages with `last_synced` older than the newest cited source's `updated`.
- Contradictions: same fact stated differently across pages.
- Concepts mentioned on >2 pages without their own page.
- Stale `status: stable` pages cited only by deleted sources.

Append `log.md` entry: `## [<date>] lint | <N> issues found`.

## Index format (`index.md`)

```markdown
# Wiki Index

## Overview
- [[overview]] — system synthesis, start here

## Entities
- [[entities/payment-gateway]] — handles card auth and capture
- ...

## Concepts
- ...

## Processes
- ...

## APIs
- ...

## Decisions
- ...

## Sources
- [[sources/<mirrored-path>]] — <one-line>
```

Keep entries one line each. Sort alphabetically inside each section.

## Log format (`log.md`)

Append-only. Each entry begins with `## [<YYYY-MM-DD>] <op> | <subject>` so `grep "^## \[" log.md` works. Body is 1–3 lines.

```markdown
## [2026-05-10] cold-start | 47 sources -> 112 pages
Built initial wiki from original-spec/. 14 entities, 22 concepts, 9 processes,
31 apis, 4 decisions, 32 source mirrors. 6 open questions.

## [2026-05-12] ingest | original-spec/billing/refunds.md
Updated [[entities/refund]], [[processes/refund-flow]]. New page [[concepts/partial-refund]].
```

## Style

- Plain markdown. No HTML. No emojis unless the source has them.
- Sentences over walls of text. Bullets for enumerations only.
- Code blocks for any literal: identifiers, JSON shapes, SQL, env vars, error codes.
- Tables sparingly — only when comparing 3+ items on 3+ axes.
- Dates ISO (`YYYY-MM-DD`). Times UTC unless the spec specifies a zone.
- Quote spec wording verbatim when the wording itself matters (legal, contractual, error messages). Use `> blockquote` and cite.

## When the spec is unclear

Write what the spec says, mark the gap, move on. Format:

```
> ⚠ Gap: spec does not state <X>. See [[open-questions#qNNN]].
```

Add a numbered question to `open-questions.md`:

```markdown
## qNNN — <short title>
**Source(s):** [[src:original-spec/<path>.md]]
**Question:** <precise question>
**Why it matters:** <1 line>
**Candidate answers (if any):** <bullets, each cited or marked speculation>
```

## Definition of done (per ingest run)

- Every source under `original-spec/` is reachable from `index.md` via at most 2 hops.
- No page has empty `sources`.
- `log.md` has a new entry for the run.
- `open-questions.md` reflects current unresolved gaps.
- `grep -r "TODO\|FIXME\|<.*>" llm-wiki/` returns only intentional placeholders.

---

## How to invoke

Run this prompt with Claude Code (or any agent) at the project root after `python fetch_confluence.py` populates `original-spec/`. First run: cold-start. Subsequent runs: incremental ingest. Periodically: lint pass.
