# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Two-stage pipeline that turns a Confluence space into an LLM-maintained wiki:

1. **Fetch** — `fetch_confluence.py` pulls a Confluence space (or page subtree) and writes markdown into `original-spec/`, mirroring the Confluence page hierarchy.
2. **Convert** — Claude (driven by `prompts/convert-to-wiki.md`) reads `original-spec/` as immutable source-of-truth and incrementally builds/maintains a structured wiki under `llm-wiki/`.

`LLM-wiki-prompt-idea.md` is the conceptual background for stage 2 (persistent, compounding wiki maintained by an LLM rather than RAG-style query-time retrieval). Read it before changing the conversion prompt.

## Commands

```bash
# install (Python ≥ 3.13)
uv sync                        # or: pip install -e .

# configure
cp .env.example .env           # fill base url, email, scoped API token

# fetch a whole space
python fetch_confluence.py --space ENG

# fetch only a subtree
python fetch_confluence.py --root-page-id 123456789

# custom output dir
python fetch_confluence.py --space ENG --output spec-snapshot/
```

No test suite, no linter configured. If you add code, keep it stdlib + the four declared deps unless there's a real need.

## Architecture

### Stage 1: `fetch_confluence.py`

Single-file CLI. Confluence Cloud REST API **v2** only (`/wiki/api/v2/...`). Auth: HTTP Basic with email + scoped API token.

Hierarchy preservation rule (this is the load-bearing invariant):
- A page **with children** becomes a directory `<slug>/` containing `index.md` for the page itself.
- A **leaf** page becomes `<slug>.md` in its parent's directory.
- Slugs are derived from titles (`slugify`); collisions append the page id.

Each emitted markdown file has YAML frontmatter (`title`, `confluence_id`, `space_id`, `confluence_url`, `version`, `updated`). The conversion stage relies on `confluence_id` and `updated` to detect changes between runs — do not drop those fields.

Body conversion: storage-format XHTML → markdown via `markdownify`. Two-pass design (`collect` then write):
1. **Pass 1 (`collect`)** walks the page tree, fetches bodies, and builds a `registry: page_id -> (target_path, page, body_html)` plus a `skipped_empty` list. Empty pages (no visible text, no images/tables/macros — see `is_empty_body`) are skipped: empty leaves produce no file; empty parents that have children produce no `index.md` but their subtree still gets written.
2. **Pass 2** rewrites links via `process_links` and writes files. `process_links` uses the registry's `id_to_path` map to convert Confluence page links (`/wiki/spaces/<KEY>/pages/<ID>/...`, absolute or relative) into **relative markdown paths** between local files. Links to pages outside the fetched scope (or to skipped empty pages) are converted to absolute URLs so they remain clickable.

Robustness details worth preserving:
- `_paginate` follows `_links.next` from v2 responses; query params only sent on the first page (next link already encodes them).
- `_request` retries on HTTP 429 with `Retry-After` (exponential fallback up to 5 attempts), exits with a diagnostic hint on 401.
- `collect` carries a `seen` set to prevent cycles if Confluence ever returns the same page twice.
- Two passes are required because link rewriting needs the full `id_to_path` map; do not collapse them back into a single pass.

### Stage 2: `prompts/convert-to-wiki.md`

This file *is* the contract for Claude's behavior in stage 2. It defines:
- Output layout (`entities/`, `concepts/`, `processes/`, `apis/`, `decisions/`, `sources/`, plus `index.md`/`log.md`/`overview.md`/`glossary.md`/`open-questions.md`).
- Page frontmatter schema (`type`, `status`, `sources`, `last_synced`, `tags`).
- Citation requirement: every claim cites a source path; uncited synthesis is forbidden.
- Workflows: cold-start, incremental ingest (uses source `version`/`updated` vs page `last_synced` to detect changes), and lint pass.
- `log.md` line format `## [YYYY-MM-DD] <op> | <subject>` so it stays grep-parseable.

When editing this prompt, keep it self-contained — it must work when copied into `llm-wiki/CLAUDE.md` for the wiki dir, with no external context.

### Directory layout

| Path | Owner | Mutability |
|------|-------|------------|
| `fetch_confluence.py`, `pyproject.toml`, `prompts/` | code | edit normally |
| `original-spec/` | fetch script | regenerated; never hand-edit |
| `llm-wiki/` | Claude (stage 2) | LLM-owned; humans read |
| `.env` | user | secrets; gitignored |

`original-spec/` and `llm-wiki/` are gitignored — they are generated artifacts, not source.

## Conventions

- Stage 1 must never mutate `original-spec/` content beyond rewriting it from Confluence; stage 2 must never mutate `original-spec/` at all.
- API token scopes (when using a scoped token): `read:page:confluence`, `read:space:confluence`, `read:hierarchical-content:confluence`, `read:content-details:confluence`. No write scopes — the fetcher is read-only.
- Filenames are slugified ASCII; non-ASCII titles still get a usable slug because `slugify` falls back to the page id.
