"""Incremental compile — daily logs → concept / connection / Q&A articles.

Algorithm (plan §memory-extraction-pipeline):

1. Hash every daily log. Anything whose sha differs from ``state.json`` is
   dirty.
2. Articles whose recorded source hashes no longer match what's on disk are
   also dirty (daily log changed → regenerate articles it contributed to).
3. For each dirty daily, one LLM call extracts a list of *candidates*
   ``{kind, slug, title, tags, summary, related}``.
4. Slug determines identity. If ``concepts/<slug>.md`` exists we **merge**:
   another LLM call rewrites the article to incorporate new contributions
   while preserving prior content. Otherwise we **create** a fresh article
   from the summary.
5. Frontmatter (sources, tags, updated, cost_usd) is owned by Python — the
   LLM only writes the body.
6. ``## Backlinks`` is rebuilt deterministically at the end by scanning every
   article for ``[[wikilinks]]``.
7. ``knowledge/index.md`` is regenerated (see ``memory.index``).
8. Commit in the knowledge/ git repo (init on first compile).

This module intentionally does not use filesystem tools on the LLM side —
Python owns all I/O and decides identity. The LLM is a pure text
transformer. That's cheaper and easier to test than letting an agent loose
with Write permissions.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ..config import Config, load_config
from ..log import get_logger, log_event
from ..paths import ProjectPaths, UserPaths, user_paths
from ._shared import (
    KINDS,
    Kind,
    parse_frontmatter,
    strip_backlinks,
    wikilink_targets,
)
from ._shared import (
    first_paragraph as _first_paragraph,  # noqa: F401 — re-exported for tests/callers
)
from .agent import AgentClient, AgentRequest, complete_sync, default_client
from .index import write_index
from .state import (
    CompileState,
    sha256_of_file,
    sha256_of_text,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")

EXTRACTION_SYSTEM_PROMPT = """\
You extract durable knowledge candidates from one day's session log.

Return a JSON array. Each candidate is an object:
  {
    "kind": "concept" | "connection" | "qa",
    "slug": "kebab-case-stem",
    "title": "Short human title",
    "tags": ["short", "lowercase", "tags"],
    "summary": "2–4 sentence self-contained summary suitable as article body",
    "related": ["other-slug", ...]
  }

Rules:
- Include only durable, reusable knowledge. Skip ephemeral chatter,
  routine commands, or one-off debugging that taught nothing general.
- "concept" = a named idea, tool, pattern, invariant.
- "connection" = a relationship between two concepts worth remembering.
- "qa" = a specific question with a specific answer (including rationale).
- Slugs must be kebab-case, lowercase, no punctuation.
- At most 6 candidates. Prefer fewer high-quality than many thin.
- If nothing durable: return [].

Output: JSON array only. No preamble, no trailing text, no code fence.
"""

MERGE_SYSTEM_PROMPT = """\
You write a compact Markdown knowledge article body.

You will receive:
- Title and kind of the article.
- Existing article body (may be empty on first write).
- One or more NEW contributions from recent sessions.

Produce the merged body only:
- No frontmatter (I add it).
- No top-level heading (I add it).
- Preserve durable content from the existing body; integrate new contributions without losing prior facts.
- If existing and new disagree, surface the conflict briefly rather than silently picking one.
- Use `[[other-slug]]` wikilinks to cross-reference related concepts where natural.
- Keep it tight — aim for 100–400 words unless the subject truly demands more.
- Never invent content beyond what the inputs support.

Do not include a ## Backlinks section. That is regenerated deterministically.
"""


# ── data shapes ───────────────────────────────────────────────────────────


@dataclass
class Candidate:
    kind: Kind
    slug: str
    title: str
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    related: list[str] = field(default_factory=list)
    source_daily: str = ""  # rel path of the daily that produced it

    @property
    def rel_path(self) -> str:
        sub = {"concept": "concepts", "connection": "connections", "qa": "qa"}[self.kind]
        return f"knowledge/{sub}/{self.slug}.md"


@dataclass
class CompileResult:
    articles_created: list[str] = field(default_factory=list)
    articles_updated: list[str] = field(default_factory=list)
    articles_unchanged: list[str] = field(default_factory=list)
    dirty_daily: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    git_sha: str | None = None


# ── helpers ───────────────────────────────────────────────────────────────


def _slugify(raw: str) -> str:
    return _SLUG_RE.sub("-", raw.lower()).strip("-") or "untitled"


def _extract_json_array(text: str) -> list[Any]:
    """Salvage a JSON array from LLM output, tolerating code fences + prose."""
    stripped = text.strip()
    # Strip trailing/leading fences if present.
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n", "", stripped)
        stripped = re.sub(r"\n```\s*$", "", stripped)
    # Find the first [...] blob.
    m = re.search(r"\[.*\]", stripped, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _parse_candidates(raw: list[Any], source_daily: str) -> list[Candidate]:
    out: list[Candidate] = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        kind_raw = d.get("kind")
        if kind_raw not in KINDS:
            continue
        kind: Kind = kind_raw
        slug = _slugify(str(d.get("slug") or d.get("title") or ""))
        title = str(d.get("title") or slug.replace("-", " ").title()).strip()
        tags = [str(t).strip().lower() for t in (d.get("tags") or []) if str(t).strip()]
        summary = str(d.get("summary") or "").strip()
        related = [_slugify(str(r)) for r in (d.get("related") or [])]
        if not slug or not summary:
            continue
        out.append(
            Candidate(
                kind=kind,
                slug=slug,
                title=title,
                tags=tags,
                summary=summary,
                related=related,
                source_daily=source_daily,
            )
        )
    return out


def _strip_leading_h1(body: str) -> str:
    """Strip a leading ``# Title`` line — we re-emit one from frontmatter."""
    return re.sub(r"^#\s+[^\n]*\n+", "", body.lstrip(), count=1)


def _render_article(
    *,
    title: str,
    kind: Kind,
    tags: Iterable[str],
    created: str,
    updated: str,
    sources: Iterable[str],
    cost_usd: float | None,
    body: str,
    backlinks: Iterable[str],
) -> str:
    tags_yaml = "[" + ", ".join(sorted({t for t in tags})) + "]"
    sources_block = "\n".join(f"  - {s}" for s in sorted(set(sources)))
    cost_line = f"cost_usd: {cost_usd:.4f}" if cost_usd is not None else "cost_usd: null"
    backlinks_section = ""
    unique_backlinks = sorted(set(backlinks))
    if unique_backlinks:
        lines = "\n".join(f"- [[{b}]]" for b in unique_backlinks)
        backlinks_section = f"\n\n## Backlinks\n{lines}"
    body_clean = _strip_leading_h1(strip_backlinks(body.strip()))
    return (
        "---\n"
        f"title: {title}\n"
        f"kind: {kind}\n"
        f"tags: {tags_yaml}\n"
        f"created: {created}\n"
        f"updated: {updated}\n"
        f"sources:\n{sources_block}\n"
        f"{cost_line}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body_clean}"
        f"{backlinks_section}\n"
    )


def _today_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _relpath(full: Path, base: Path) -> str:
    return str(full.relative_to(base))


# ── git ───────────────────────────────────────────────────────────────────


def _run_git(knowledge_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(knowledge_dir), *args],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )


def _ensure_git(knowledge_dir: Path) -> None:
    if (knowledge_dir / ".git").exists():
        return
    _run_git(knowledge_dir, "init", "-q")
    # User-level git config is fine; we just need a committer identity.
    name = _run_git(knowledge_dir, "config", "user.name").stdout.strip()
    if not name:
        _run_git(knowledge_dir, "config", "user.name", "adjoint")
        _run_git(knowledge_dir, "config", "user.email", "adjoint@localhost")


def _commit(knowledge_dir: Path, message: str) -> str | None:
    _run_git(knowledge_dir, "add", "-A")
    status = _run_git(knowledge_dir, "status", "--porcelain").stdout.strip()
    if not status:
        return None
    cp = _run_git(knowledge_dir, "commit", "-q", "-m", message)
    if cp.returncode != 0:
        return None
    return _run_git(knowledge_dir, "rev-parse", "HEAD").stdout.strip() or None


# ── core compile ──────────────────────────────────────────────────────────


def _daily_logs(pp: ProjectPaths) -> list[Path]:
    if not pp.daily_dir.is_dir():
        return []
    return sorted(pp.daily_dir.glob("*.md"))


def _extract_candidates(
    *, agent: AgentClient, cfg: Config, daily_path: Path, source_rel: str
) -> tuple[list[Candidate], float]:
    text = daily_path.read_text(encoding="utf-8")
    req = AgentRequest(
        system=EXTRACTION_SYSTEM_PROMPT,
        user=f"Daily log {daily_path.name}:\n\n{text}\n\nReturn JSON.",
        model=cfg.model_for("claude"),
        allowed_tools=[],
        max_turns=1,
        recursion_tag="adjoint_compile",
    )
    resp = complete_sync(agent, req)
    raw = _extract_json_array(resp.text)
    return _parse_candidates(raw, source_rel), (resp.cost_usd or 0.0)


def _render_body(
    *,
    agent: AgentClient,
    cfg: Config,
    title: str,
    kind: Kind,
    existing_body: str,
    contributions: list[str],
) -> tuple[str, float]:
    contrib_block = "\n".join(f"- {c}" for c in contributions)
    user = (
        f"Title: {title}\n"
        f"Kind: {kind}\n\n"
        f"Existing body:\n{existing_body or '_empty_'}\n\n"
        f"New contributions:\n{contrib_block}\n"
    )
    req = AgentRequest(
        system=MERGE_SYSTEM_PROMPT,
        user=user,
        model=cfg.model_for("claude"),
        allowed_tools=[],
        max_turns=1,
        recursion_tag="adjoint_compile",
    )
    resp = complete_sync(agent, req)
    return resp.text.strip(), (resp.cost_usd or 0.0)


@dataclass
class _CachedArticle:
    path: Path
    text: str
    fm: dict[str, str]
    body: str


def _load_articles_on_disk(pp: ProjectPaths) -> list[_CachedArticle]:
    """Read every article on disk once — pass 2 and backlink scan share the cache."""
    out: list[_CachedArticle] = []
    for sub in (pp.concepts_dir, pp.connections_dir, pp.qa_dir):
        if not sub.is_dir():
            continue
        for p in sub.glob("*.md"):
            text = p.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(text)
            out.append(_CachedArticle(path=p, text=text, fm=fm, body=body))
    return out


def _collect_backlinks(pp: ProjectPaths, cache: list[_CachedArticle]) -> dict[str, list[str]]:
    """Map slug → list of article rel_paths that wikilink to it."""
    back: dict[str, list[str]] = defaultdict(list)
    for art in cache:
        rel = _relpath(art.path, pp.knowledge_dir)
        for target in wikilink_targets(art.text):
            if target != art.path.stem:
                back[target].append(rel)
    return {k: sorted(set(v)) for k, v in back.items()}


def compile_project(
    *,
    project_path: Path,
    mode: Literal["incremental", "full"] = "incremental",
    config: Config | None = None,
    client: AgentClient | None = None,
    paths: UserPaths | None = None,
    dry_run: bool = False,
) -> CompileResult:
    cfg = config or load_config(project_path)
    agent = client or default_client()
    up = paths or user_paths()
    pp = up.project(project_path)
    pp.ensure()
    logger = get_logger("memory.compile")

    state = CompileState.load(pp.state_json)
    daily_paths = _daily_logs(pp)

    # Dirty sets.
    dirty_daily = daily_paths if mode == "full" else state.dirty_daily_logs(daily_paths, pp.root)
    dirty_articles_rel = set(state.dirty_articles(pp.root))

    result = CompileResult(
        dirty_daily=[_relpath(p, pp.root) for p in dirty_daily],
    )

    # Candidates grouped by article rel path.
    candidates_by_article: dict[str, list[Candidate]] = defaultdict(list)
    daily_hash_updates: dict[str, tuple[str, float]] = {}
    total_cost = 0.0

    for daily in dirty_daily:
        rel = _relpath(daily, pp.root)
        cands, cost = _extract_candidates(agent=agent, cfg=cfg, daily_path=daily, source_rel=rel)
        total_cost += cost
        daily_hash_updates[rel] = (sha256_of_file(daily), cost)
        for c in cands:
            candidates_by_article[c.rel_path].append(c)

    # Articles to (re)write = articles that got new candidates PLUS articles
    # previously marked dirty because a source changed (even if this run
    # produces no candidates for them, we regenerate frontmatter + backlinks).
    target_articles: set[str] = set(candidates_by_article.keys()) | dirty_articles_rel

    if dry_run:
        log_event(
            logger,
            "compile.dry_run",
            dirty_daily=result.dirty_daily,
            target_articles=sorted(target_articles),
            estimated_cost_usd=total_cost,
        )
        result.cost_usd = total_cost
        return result

    # Persist daily hashes so a rerun without changes is a no-op.
    for rel, (sha, cost) in daily_hash_updates.items():
        state.record_daily(rel, sha, cost)

    # Write / update articles.
    for art_rel in sorted(target_articles):
        cands = candidates_by_article.get(art_rel, [])
        full = pp.root / art_rel
        creating = not full.is_file()

        if creating and not cands:
            # Previously dirty article but no fresh candidate → skip silently.
            result.articles_unchanged.append(art_rel)
            continue

        # Pull existing frontmatter + body if present.
        existing_body = ""
        existing_fm: dict[str, str] = {}
        if not creating:
            existing_fm, existing_body = parse_frontmatter(full.read_text(encoding="utf-8"))

        # Kind, title, tags: prefer the freshest candidate (last in list), else
        # fall back to existing frontmatter.
        canonical = cands[-1] if cands else None
        kind: Kind = canonical.kind if canonical else existing_fm.get("kind", "concept")  # type: ignore[assignment]
        title = canonical.title if canonical else existing_fm.get("title", full.stem)
        tags: set[str] = set()
        for c in cands:
            tags.update(c.tags)
        # Preserve previous tags.
        if existing_fm.get("tags"):
            for t in re.findall(r"[a-z0-9_-]+", existing_fm["tags"]):
                tags.add(t)

        contributions = [c.summary for c in cands]
        if not contributions:
            # Article dirty but no new content — rewrite frontmatter only, body unchanged.
            new_body = existing_body
            call_cost = 0.0
        else:
            new_body, call_cost = _render_body(
                agent=agent,
                cfg=cfg,
                title=title,
                kind=kind,
                existing_body=strip_backlinks(existing_body),
                contributions=contributions,
            )
            total_cost += call_cost

        # Sources: union of existing + new contributions' daily.
        sources: set[str] = set()
        existing_entry = state.articles.get(art_rel)
        if existing_entry:
            sources.update(existing_entry.sources)
        for c in cands:
            sources.add(c.source_daily)

        rendered = _render_article(
            title=title,
            kind=kind,
            tags=tags,
            created=(existing_fm.get("created") or _today_iso()) if not creating else _today_iso(),
            updated=_today_iso(),
            sources=sources,
            cost_usd=call_cost or None,
            body=new_body,
            backlinks=[],  # filled in pass 2
        )
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(rendered, encoding="utf-8")
        source_hashes = {
            s: (
                sha256_of_text((pp.root / s).read_text(encoding="utf-8"))
                if (pp.root / s).is_file()
                else ""
            )
            for s in sources
        }
        state.record_article(
            art_rel,
            sorted(sources),
            source_hashes,
            creating=creating,
        )
        (result.articles_created if creating else result.articles_updated).append(art_rel)

    # Pass 2: rebuild ``## Backlinks`` deterministically. Every article is read
    # exactly once into ``article_cache``; both the backlink scan and the
    # rewrite loop use the cache. We only write back when the rendered output
    # actually differs — this keeps incremental no-op runs fully idempotent
    # (no spurious git commits).
    article_cache = _load_articles_on_disk(pp)
    back = _collect_backlinks(pp, article_cache)
    for art in article_cache:
        p, current, fm, body = art.path, art.text, art.fm, art.body
        kind_raw = fm.get("kind", "concept")
        kind_val: Kind = kind_raw if kind_raw in KINDS else "concept"
        tags_val = re.findall(r"[a-z0-9_-]+", fm.get("tags", "")) or []
        entry = state.articles.get(_relpath(p, pp.root))
        sources_val = entry.sources if entry else ["daily/unknown.md"]
        rendered = _render_article(
            title=fm.get("title", p.stem),
            kind=kind_val,
            tags=tags_val,
            created=fm.get("created", _today_iso()),
            updated=fm.get("updated", _today_iso()),
            sources=sources_val,
            cost_usd=None,
            body=strip_backlinks(body),
            backlinks=back.get(p.stem, []),
        )
        if rendered != current:
            p.write_text(rendered, encoding="utf-8")

    # Regenerate index.
    write_index(pp)

    # Persist state + commit.
    state.save(pp.state_json)
    _ensure_git(pp.knowledge_dir)
    message = (
        f"compile: {_today_iso()} "
        f"(new={len(result.articles_created)}, updated={len(result.articles_updated)}, "
        f"dirty_daily={len(result.dirty_daily)})"
    )
    result.git_sha = _commit(pp.knowledge_dir, message)
    result.cost_usd = total_cost

    log_event(
        logger,
        "compile.ok",
        dirty_daily=len(result.dirty_daily),
        articles_created=result.articles_created,
        articles_updated=result.articles_updated,
        cost_usd=result.cost_usd,
        git_sha=result.git_sha,
    )
    return result
