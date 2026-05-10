#!/usr/bin/env python3
"""Fetch Confluence pages from a space or root page tree, save as markdown.

Output preserves the Confluence page hierarchy: a page with children becomes
a directory with index.md, leaf pages become <slug>.md.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from markdownify import markdownify as html_to_md

SLUG_STRIP = re.compile(r"[^\w\-]+")
COLLAPSE_DASH = re.compile(r"-+")


def slugify(title: str, fallback: str = "untitled") -> str:
    s = title.strip().lower().replace(" ", "-")
    s = SLUG_STRIP.sub("", s)
    s = COLLAPSE_DASH.sub("-", s).strip("-")
    return s or fallback


def yaml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


class ConfluenceClient:
    def __init__(self, base_url: str, email: str | None, token: str, auth_mode: str = "auto"):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

        # auth_mode: "basic" (classic token + email), "bearer" (scoped token), "auto"
        # auto: bearer if no email provided, else basic.
        if auth_mode == "auto":
            auth_mode = "bearer" if not email else "basic"
        self.auth_mode = auth_mode
        if auth_mode == "bearer":
            self.session.headers["Authorization"] = f"Bearer {token}"
        elif auth_mode == "basic":
            if not email:
                raise SystemExit("Basic auth requires CONFLUENCE_EMAIL.")
            self.session.auth = (email, token)
        else:
            raise SystemExit(f"Unknown auth mode: {auth_mode}")

    def _request(self, url: str, params: dict | None = None) -> dict:
        for attempt in range(5):
            r = self.session.get(url, params=params, timeout=30)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 2 ** attempt))
                time.sleep(wait)
                continue
            if r.status_code == 401:
                hint = (
                    "Auth failed (401). Hints:\n"
                    f"  - auth mode in use: {self.auth_mode}\n"
                    "  - scoped API tokens (with scope picker) require Bearer auth: "
                    "set CONFLUENCE_AUTH=bearer and leave CONFLUENCE_EMAIL empty\n"
                    "  - classic API tokens require Basic auth: set CONFLUENCE_EMAIL "
                    "and CONFLUENCE_AUTH=basic (or unset)\n"
                    "  - check token belongs to the same Atlassian site as the base URL\n"
                    "  - check for stray whitespace/quotes in .env values"
                )
                raise SystemExit(hint)
            r.raise_for_status()
            return r.json()
        r.raise_for_status()
        return {}

    def _get(self, path: str, params: dict | None = None) -> dict:
        return self._request(f"{self.base}{path}", params=params)

    def _paginate(self, path: str, params: dict | None = None) -> Iterator[dict]:
        url = f"{self.base}{path}"
        first = True
        while url:
            data = self._request(url, params=params if first else None)
            yield from data.get("results", [])
            next_link = data.get("_links", {}).get("next")
            if not next_link:
                break
            # next link from v2 is relative path including query string
            url = f"{self.base}{next_link}"
            first = False

    def get_space_id(self, space_key: str) -> str:
        data = self._get("/wiki/api/v2/spaces", params={"keys": space_key})
        results = data.get("results") or []
        if not results:
            raise SystemExit(f"Confluence space '{space_key}' not found or no access.")
        return results[0]["id"]

    def get_space_root_pages(self, space_id: str) -> list[dict]:
        return list(
            self._paginate(
                f"/wiki/api/v2/spaces/{space_id}/pages",
                params={"depth": "root", "limit": 250},
            )
        )

    def get_page(self, page_id: str) -> dict:
        return self._get(
            f"/wiki/api/v2/pages/{page_id}",
            params={"body-format": "storage"},
        )

    def get_children(self, page_id: str) -> list[dict]:
        return list(
            self._paginate(
                f"/wiki/api/v2/pages/{page_id}/children",
                params={"limit": 250},
            )
        )


PAGE_LINK_PATTERNS = [
    re.compile(r"/wiki/spaces/[^/]+/pages/(\d+)"),
    re.compile(r"[?&]pageId=(\d+)"),
]


def extract_page_id(href: str) -> str | None:
    for p in PAGE_LINK_PATTERNS:
        m = p.search(href)
        if m:
            return m.group(1)
    return None


def is_empty_body(html: str) -> bool:
    if not html or not html.strip():
        return True
    soup = BeautifulSoup(html, "html.parser")
    # treat as empty if no visible text and no images/embeds/tables
    if soup.find(["img", "table", "iframe", "ac:image", "ac:structured-macro"]):
        return False
    return not soup.get_text(strip=True)


def flatten_confluence_macros(
    html: str,
    base_url: str,
    title_to_id: dict[str, str],
    id_to_title: dict[str, str] | None = None,
) -> str:
    """Replace Confluence storage-format link macros (<ac:inline-card>, <ac:link>)
    with plain <a href> anchors so markdownify renders them and process_links can
    rewrite them to local paths."""
    if not html:
        return html
    id_to_title = id_to_title or {}
    soup = BeautifulSoup(html, "html.parser")

    # smart inline cards: <ac:inline-card ac:href="https://.../pages/123"/>
    for card in list(soup.find_all(["ac:inline-card", "ac:link-card"])):
        href = card.get("ac:href") or card.get("href")
        if not href:
            card.decompose()
            continue
        a = soup.new_tag("a", href=href)
        # prefer the linked page's title as link text (matches how Confluence renders cards)
        target_id = extract_page_id(href)
        text = card.get_text(strip=True)
        if not text and target_id and target_id in id_to_title:
            text = id_to_title[target_id]
        a.string = text or href
        card.replace_with(a)

    # classic <ac:link><ri:page .../><ac:link-body>text</ac:link-body></ac:link>
    for link in list(soup.find_all("ac:link")):
        page = link.find("ri:page")
        url: str | None = None
        text: str | None = None
        if page is not None:
            content_id = page.get("ri:content-id")
            content_title = page.get("ri:content-title")
            space_key = page.get("ri:space-key")
            if not content_id and content_title and content_title in title_to_id:
                content_id = title_to_id[content_title]
            if content_id:
                url = f"{base_url}/wiki/pages/viewpage.action?pageId={content_id}"
            elif content_title and space_key:
                url = f"{base_url}/wiki/spaces/{space_key}/pages/{content_title.replace(' ', '+')}"
            text = content_title
        body = link.find("ac:link-body") or link.find("ac:plain-text-link-body")
        if body is not None:
            body_text = body.get_text(strip=True)
            if body_text:
                text = body_text
        text = text or url or "link"
        if url:
            a = soup.new_tag("a", href=url)
            a.string = text
            link.replace_with(a)
        else:
            link.replace_with(text)

    # unwrap remaining ac:* / ri:* tags so markdownify doesn't choke
    for tag in list(soup.find_all(lambda t: t.name and (t.name.startswith("ac:") or t.name.startswith("ri:")))):
        tag.unwrap()

    return str(soup)


def process_links(
    html: str,
    base_url: str,
    current_target: Path,
    id_to_path: dict[str, Path],
) -> str:
    """Rewrite Confluence page links to relative local paths when target is in registry,
    otherwise convert relative confluence URLs to absolute so they remain usable."""
    if not html:
        return html
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        path_part = href[len(base_url):] if href.startswith(base_url) else href
        target_id = extract_page_id(path_part)
        if target_id:
            target_path = id_to_path.get(target_id)
            if target_path:
                rel = os.path.relpath(target_path, current_target.parent)
                a["href"] = rel
                continue
        if href.startswith("/"):
            a["href"] = f"{base_url}{href}"
    return str(soup)


def build_frontmatter(page: dict, base_url: str) -> str:
    title = page.get("title", "Untitled")
    page_id = str(page.get("id", ""))
    space_id = page.get("spaceId", "")
    version = page.get("version", {}) or {}
    web_path = (page.get("_links", {}) or {}).get("webui", "")
    web_url = f"{base_url}/wiki{web_path}" if web_path else (
        f"{base_url}/wiki/spaces/{space_id}/pages/{page_id}" if space_id else ""
    )
    lines = [
        "---",
        f'title: "{yaml_escape(title)}"',
        f"confluence_id: {page_id}",
        f"space_id: {space_id}",
        f"confluence_url: {web_url}",
        f"version: {version.get('number', '')}",
        f"updated: {version.get('createdAt', '')}",
        "---",
        "",
    ]
    return "\n".join(lines)


def write_page_file(target: Path, page: dict, body_html: str, base_url: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    title = page.get("title", "Untitled")
    md = html_to_md(body_html, heading_style="ATX", bullets="-").strip()
    fm = build_frontmatter(page, base_url=base_url)
    target.write_text(f"{fm}\n# {title}\n\n{md}\n", encoding="utf-8")


def collect(
    client: ConfluenceClient,
    page_stub: dict,
    dir_path: Path,
    seen: set[str],
    registry: dict[str, tuple[Path, dict, str]],
    skipped_empty: list[str],
) -> None:
    """Walk the page tree, fill the registry with (target_path, page, body_html).
    Skip pages whose body is empty: leaf -> not written; parent -> dir created via
    children but no index.md."""
    page_id = str(page_stub["id"])
    if page_id in seen:
        return
    seen.add(page_id)

    page = client.get_page(page_id)
    title = page.get("title", "Untitled")
    slug = slugify(title, fallback=page_id)
    children = client.get_children(page_id)
    body_html = ((page.get("body") or {}).get("storage") or {}).get("value", "") or ""
    empty = is_empty_body(body_html)

    if children:
        sub_dir = dir_path / slug
        if empty:
            skipped_empty.append(f"{sub_dir}/index.md (empty parent)")
        else:
            target = sub_dir / "index.md"
            registry[page_id] = (target, page, body_html)
        for child in children:
            collect(client, child, sub_dir, seen, registry, skipped_empty)
    else:
        if empty:
            skipped_empty.append(f"{dir_path}/{slug}.md (empty leaf)")
            return
        target = dir_path / f"{slug}.md"
        # avoid colliding with a sibling index.md or duplicate slug
        if target.exists() or any(t == target for t, _, _ in registry.values()):
            target = dir_path / f"{slug}-{page_id}.md"
        registry[page_id] = (target, page, body_html)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--space", default=os.getenv("CONFLUENCE_SPACE_KEY"), help="Confluence space key")
    p.add_argument("--root-page-id", default=os.getenv("CONFLUENCE_ROOT_PAGE_ID"), help="Root page id (takes precedence over --space)")
    p.add_argument("--output", default="original-spec", help="Output directory (default: original-spec)")
    p.add_argument("--base-url", default=os.getenv("CONFLUENCE_BASE_URL"), help="Confluence base URL, e.g. https://yourorg.atlassian.net")
    return p.parse_args()


def _clean_env(name: str) -> str | None:
    v = os.getenv(name)
    if v is None:
        return None
    v = v.strip().strip('"').strip("'")
    return v or None


def main() -> int:
    load_dotenv()
    args = parse_args()

    email = _clean_env("CONFLUENCE_EMAIL")
    token = _clean_env("CONFLUENCE_API_TOKEN")
    auth_mode = (_clean_env("CONFLUENCE_AUTH") or "auto").lower()
    base_url = (args.base_url or "").rstrip("/").strip().strip('"').strip("'")
    if not (base_url and token):
        sys.exit("Missing env: CONFLUENCE_BASE_URL, CONFLUENCE_API_TOKEN")
    if not (args.space or args.root_page_id):
        sys.exit("Provide --space or --root-page-id (or set env equivalents).")
    if not urlparse(base_url).scheme:
        sys.exit(f"CONFLUENCE_BASE_URL must include scheme, got: {base_url}")

    client = ConfluenceClient(base_url, email, token, auth_mode=auth_mode)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    registry: dict[str, tuple[Path, dict, str]] = {}
    skipped_empty: list[str] = []

    # pass 1 — walk Confluence, collect pages, decide target paths, skip empties
    if args.root_page_id:
        print(f"Fetching subtree from root page {args.root_page_id} -> {out}")
        collect(client, {"id": args.root_page_id}, out, seen, registry, skipped_empty)
    else:
        print(f"Fetching space '{args.space}' -> {out}")
        space_id = client.get_space_id(args.space)
        roots = client.get_space_root_pages(space_id)
        if not roots:
            sys.exit(f"No root pages in space {args.space}.")
        for root in roots:
            collect(client, root, out, seen, registry, skipped_empty)

    # build maps for cross-page link rewriting
    id_to_path: dict[str, Path] = {pid: t for pid, (t, _, _) in registry.items()}
    id_to_title: dict[str, str] = {pid: p.get("title", "") for pid, (_, p, _) in registry.items()}
    title_to_id: dict[str, str] = {t: pid for pid, t in id_to_title.items() if t}

    # pass 2 — flatten Confluence macros, rewrite links, write files
    for page_id, (target, page, body_html) in registry.items():
        flattened = flatten_confluence_macros(body_html, base_url, title_to_id, id_to_title)
        rewritten = process_links(flattened, base_url, target, id_to_path)
        write_page_file(target, page, rewritten, base_url)
        print(f"  wrote {target}")

    print(
        f"Done. wrote {len(registry)} pages, "
        f"skipped {len(skipped_empty)} empty pages -> {out.resolve()}"
    )
    if skipped_empty:
        print("skipped empty:")
        for s in skipped_empty:
            print(f"  - {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
