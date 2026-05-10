# confluence-to-llm-wiki

Two-stage pipeline that turns a Confluence space into an LLM-maintained wiki.

1. **Fetch** — `fetch_confluence.py` pulls every page from a Confluence space (or page subtree) and writes it to `original-spec/` as markdown, preserving the page hierarchy.
2. **Convert** — Claude reads `original-spec/` (immutable source-of-truth) and incrementally builds/maintains `llm-wiki/` — a structured, cross-linked knowledge base with citations.

The pattern is described in `LLM-wiki-prompt-idea.md`. The wiki is a *persistent, compounding artifact* — knowledge is compiled once and kept current, not re-derived on every query.

---

## Quick start

```bash
# 1. install
uv sync                                  # or: pip install -e .

# 2. configure
cp .env.example .env
$EDITOR .env                             # fill base url, token, auth mode

# 3. fetch Confluence -> original-spec/
uv run python fetch_confluence.py --space NE

# 4. open Claude Code, run the cold-start prompt (below)
```

---

## Setup

### Requirements

- Python ≥ 3.13
- `uv` (recommended) or `pip`
- Atlassian Confluence Cloud account with read access to the target space

### Install

```bash
uv sync
# or
pip install -e .
```

Deps: `requests`, `markdownify`, `python-dotenv`, `beautifulsoup4`.

### Configure `.env`

```env
CONFLUENCE_BASE_URL=https://yourorg.atlassian.net
CONFLUENCE_EMAIL=                # leave empty for scoped tokens
CONFLUENCE_API_TOKEN=...
CONFLUENCE_AUTH=auto             # auto | basic | bearer
CONFLUENCE_SPACE_KEY=NE          # or set CONFLUENCE_ROOT_PAGE_ID
CONFLUENCE_ROOT_PAGE_ID=
```

#### Token types

| Token type | Scopes UI | Auth header | `CONFLUENCE_AUTH` | `CONFLUENCE_EMAIL` |
|---|---|---|---|---|
| **Classic API token** | none (full user perms) | Basic `email:token` | `basic` (or `auto`) | required |
| **Scoped API token** | scope picker | Bearer `<token>` | `bearer` (or `auto`) | leave empty |

`auto`: bearer if email empty, basic otherwise.

Create token at https://id.atlassian.com/manage-profile/security/api-tokens.

#### Scoped token: minimum scopes

```
read:page:confluence
read:space:confluence
read:hierarchical-content:confluence
read:content-details:confluence
```

No write scopes — fetcher is read-only.

---

## Stage 1 — fetch

```bash
# whole space
uv run python fetch_confluence.py --space NE

# subtree only (Confluence page id from URL)
uv run python fetch_confluence.py --root-page-id 4975525892

# custom output dir
uv run python fetch_confluence.py --space NE --output spec-snapshot/

# override base url at CLI
uv run python fetch_confluence.py --space NE --base-url https://other.atlassian.net
```

### What it does

- Walks the Confluence page tree recursively.
- Page **with children** → directory `<slug>/` containing `index.md`.
- **Leaf** page → `<slug>.md` in parent dir.
- **Empty pages skipped** (no text, no images, no tables, no macros). Empty parent with children → no `index.md`, but children still written.
- Each file gets YAML frontmatter:
  ```yaml
  ---
  title: "Page Title"
  confluence_id: 4975525892
  space_id: ...
  confluence_url: https://yourorg.atlassian.net/wiki/...
  version: 7
  updated: 2026-04-12T10:23:00.000Z
  ---
  ```
- **Confluence page links rewritten to relative local paths.** Inline cards (`<ac:inline-card>`) and classic link macros (`<ac:link><ri:page/></ac:link>`) are flattened to real anchors first, then resolved via the registry. Out-of-scope links stay absolute.
- HTTP 429 → exponential retry. HTTP 401 → exits with diagnostic hint.

---

## Stage 2 — convert to wiki

### Schema

The contract is `prompts/convert-to-wiki.md`. Copy it into `llm-wiki/CLAUDE.md` after the first run so future Claude sessions auto-load the schema:

```bash
mkdir -p llm-wiki
cp prompts/convert-to-wiki.md llm-wiki/CLAUDE.md
```

`llm-wiki/CLAUDE.md` defines: page schema, output folders (`entities/`, `concepts/`, `processes/`, `apis/`, `decisions/`, `sources/`), citation rules, log format, lint rules.

### Cold-start ingest (first time)

In a fresh Claude Code session at the repo root, paste:

```
Read prompts/convert-to-wiki.md and follow it.

Goal: cold-start ingest from original-spec/ into llm-wiki/.

Steps:
1. Inventory: walk original-spec/, list every file with title and frontmatter.
2. Extraction: for each source, list candidate entities, concepts,
   processes, APIs, decisions, terms. No pages written yet.
3. Deduplicate: merge synonyms, pick canonical slugs, record alternates
   in glossary.
4. Write pages: one wiki page per canonical item, every claim cited via
   [[src:original-spec/<path>]].
5. Cross-link: replace bare mentions of canonical items with relative
   markdown links between wiki pages.
6. Build navigation: index.md, glossary.md, overview.md, open-questions.md.
7. Append entry to log.md: ## [<today>] cold-start | <N> sources -> <M> pages.

Hard rules: never modify original-spec/; never invent facts; preserve
spec terminology exactly.
```

### Incremental ingest (after spec changes)

```
Read llm-wiki/CLAUDE.md and follow the Incremental ingest workflow.

Detect changes: for each file under original-spec/, compare its
frontmatter `updated` field to the `last_synced` of every wiki page
that cites it. Treat as changed if updated > last_synced, or if the
source path is new.

For each changed/new source:
- Re-read the source.
- Diff against existing wiki pages that cite it.
- Update Summary/Details/Sources of affected pages, bump last_synced.
- Flag superseded claims with `> Superseded <date>: <old> — see [[new]]`.
- Add new pages for newly introduced entities/concepts.

Update index.md, glossary.md, open-questions.md if affected.
Append entry to log.md: ## [<today>] ingest | <source path> | touched: <N> pages.

Hard rules unchanged.
```

### Lint pass

```
Read llm-wiki/CLAUDE.md and follow the Lint pass workflow.

Check and report (don't silently fix):
- Orphan pages (no inbound [[links]]).
- Dangling links ([[page]] with no target).
- Pages with empty `sources` list.
- Pages whose last_synced is older than newest cited source's updated.
- Contradictions: same fact stated differently across pages.
- Sources cited by no wiki page.
- Concepts mentioned on >2 pages without their own page.
- Wiki pages whose source files no longer exist in original-spec/.

Append entry to log.md: ## [<today>] lint | <N> issues found.
Report findings as a numbered list, then ask which to fix.
```

### Query the wiki

```
Answer this question by reading llm-wiki/. Cite the wiki pages and
the underlying sources. If the answer is novel synthesis worth
keeping, file it back as a new wiki page (concepts/<slug>.md or
similar) with proper sources and add it to the index.

Question: <your question>
```

---

## Update workflow (TL;DR)

```bash
# someone edited a Confluence page
uv run python fetch_confluence.py --space NE     # re-fetch
git diff original-spec/                           # what changed in spec
# -> open Claude Code, run "Incremental ingest" prompt above
git diff llm-wiki/                                # what Claude changed
git add -A && git commit -m "spec sync $(date +%F)"
```

`git log` is your audit trail. `llm-wiki/log.md` is the human-readable timeline maintained by Claude.

### Deletions in Confluence

The fetcher does **not** delete local files when Confluence pages disappear. To pick up deletions:

```bash
rm -rf original-spec/
uv run python fetch_confluence.py --space NE
# then run lint pass to find wiki pages whose sources no longer exist
```

---

## Project layout

```
.
├── fetch_confluence.py        # stage 1 fetcher
├── prompts/
│   └── convert-to-wiki.md     # stage 2 schema (copy to llm-wiki/CLAUDE.md)
├── original-spec/             # generated, gitignored — Confluence dump
├── llm-wiki/                  # LLM-owned, gitignored — knowledge base
├── CLAUDE.md                  # repo-level guidance for future Claude sessions
├── LLM-wiki-prompt-idea.md    # background pattern (read first)
├── pyproject.toml
└── .env.example
```

`original-spec/` and `llm-wiki/` are gitignored by default (generated artifacts). Track them in git if you want history — just remove the entries from `.gitignore`.

### Mutability

| Path | Owner | Mutable by |
|---|---|---|
| `fetch_confluence.py`, `prompts/`, `pyproject.toml` | code | humans |
| `original-spec/` | fetch script | regenerated; never hand-edit |
| `llm-wiki/` | Claude (stage 2) | LLM writes, humans read |
| `.env` | user | secrets; gitignored |

---

## Troubleshooting

### `401 Unauthorized` on first fetch

Diagnostic hints printed by the script. Common causes:

1. **Token type mismatch.** Scoped tokens need Bearer auth (`CONFLUENCE_AUTH=bearer`, no email). Classic tokens need Basic auth (`CONFLUENCE_AUTH=basic` + email).
2. **Wrong site.** Token issued under a different Atlassian account/site than `CONFLUENCE_BASE_URL`.
3. **Whitespace in `.env`.** Script strips quotes/whitespace, but double-check.
4. **Missing scopes** on a scoped token. See scope list above.

Verify outside the script:

```bash
# Bearer (scoped token)
curl -i -H "Authorization: Bearer $CONFLUENCE_API_TOKEN" \
  "$CONFLUENCE_BASE_URL/wiki/api/v2/spaces?keys=$CONFLUENCE_SPACE_KEY"

# Basic (classic token)
curl -i -u "$CONFLUENCE_EMAIL:$CONFLUENCE_API_TOKEN" \
  "$CONFLUENCE_BASE_URL/wiki/api/v2/spaces?keys=$CONFLUENCE_SPACE_KEY"
```

200 → script bug. 401 → credentials. 403 → scopes.

### Links empty / list items missing in markdown

Symptom: a numbered list in Confluence renders as `1.\n2.` with no link content.

Cause: Confluence smart links are stored as `<ac:inline-card>` macros, not `<a href>`. The fetcher flattens these before markdownify. If still broken, the macro variant may be unhandled — share the raw storage HTML and update `flatten_confluence_macros` in `fetch_confluence.py`.

### Empty pages still appearing

`is_empty_body` skips pages with no text *and* no images/tables/macros. If a page has only a structured-macro stub (e.g. ToC), it's treated as non-empty. Loosen or tighten the rule by editing `is_empty_body`.

### Rate limit (429)

Built-in: 5 retries with `Retry-After`. Large spaces (>1000 pages) may still hit limits — re-run; the script is idempotent and overwrites unchanged files.

### Confluence pages not deleted locally

Known limitation. Workaround: `rm -rf original-spec/` and re-fetch. PR welcome to add `--prune`.

---

## Architecture details

### Fetcher (`fetch_confluence.py`)

Single file. Confluence Cloud REST API **v2** only. Two-pass design:

1. **Pass 1 (`collect`)** — walks page tree, fetches bodies, decides target paths (slug, dir vs file), skips empties, builds a `registry: page_id -> (target_path, page, body_html)`.
2. **Pass 2** — for each registered page: `flatten_confluence_macros` → `process_links` → `markdownify` → write file. Two passes are required because link rewriting needs the full `id_to_path` map; cannot collapse.

Key functions:

- `is_empty_body` — empty detection (text + macros + images + tables).
- `flatten_confluence_macros` — `<ac:inline-card>` / `<ac:link>` → `<a href>`.
- `process_links` — anchor href → relative local `.md` path when target known, else absolute.
- `slugify` — title → ASCII slug; collisions append page id.

### Wiki schema (`prompts/convert-to-wiki.md`)

The schema is the contract. Read it before changing the conversion prompt. It defines page frontmatter, folder structure, citation rules, three workflows (cold-start / incremental / lint), and the log format. It must work standalone when copied into `llm-wiki/CLAUDE.md`.

---

## License

MIT — see [LICENSE](LICENSE). Use, modify, distribute freely.
