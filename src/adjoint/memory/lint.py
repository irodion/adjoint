"""Seven health checks for the knowledge base.

Checks 1–5 are pure filesystem/text analysis and always run.
Checks 6–7 issue LLM calls and only run when ``--cheap`` is NOT passed.

Output is written to ``knowledge/.lint-report.md`` so the user can open it
alongside the rest of the KB in Obsidian.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ..config import Config, load_config
from ..log import RecursionTag, get_logger, log_event
from ..paths import ProjectPaths, UserPaths, user_paths
from ._shared import first_paragraph, parse_frontmatter, strip_backlinks, wikilink_targets
from .agent import AgentClient, AgentRequest, complete_sync, default_client


@dataclass
class LintIssue:
    check: str
    severity: str  # info | warn | error
    article: str
    message: str


@dataclass
class LintReport:
    issues: list[LintIssue] = field(default_factory=list)
    articles_scanned: int = 0
    cost_usd: float = 0.0

    def by_check(self) -> dict[str, list[LintIssue]]:
        out: dict[str, list[LintIssue]] = defaultdict(list)
        for i in self.issues:
            out[i.check].append(i)
        return out


@dataclass
class _LoadedArticle:
    """Cached read of one article — populated once, passed to every check."""

    path: Path
    rel: str  # relative to knowledge_dir, for report citations
    text: str
    fm: dict[str, str]
    body: str  # frontmatter-stripped
    stripped_body: str  # further stripped of a trailing ## Backlinks section


def _load_articles(pp: ProjectPaths) -> list[_LoadedArticle]:
    out: list[_LoadedArticle] = []
    for sub in (pp.concepts_dir, pp.connections_dir, pp.qa_dir):
        if not sub.is_dir():
            continue
        for p in sorted(sub.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(text)
            out.append(
                _LoadedArticle(
                    path=p,
                    rel=str(p.relative_to(pp.knowledge_dir)),
                    text=text,
                    fm=fm,
                    body=body,
                    stripped_body=strip_backlinks(body),
                )
            )
    return out


def _parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


def _slugs_referenced_in_recent_dailies(
    pp: ProjectPaths, *, within_days: int, now: date
) -> set[str]:
    if not pp.daily_dir.is_dir():
        return set()
    cutoff = now - timedelta(days=within_days)
    hits: set[str] = set()
    for p in pp.daily_dir.glob("*.md"):
        try:
            log_date = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if log_date < cutoff:
            continue
        hits |= wikilink_targets(p.read_text(encoding="utf-8"))
    return hits


# ── Checks 1–5 ───────────────────────────────────────────────────────────


def _check_broken_wikilinks(
    articles: list[_LoadedArticle], all_slugs: set[str], report: LintReport
) -> None:
    for art in articles:
        for target in wikilink_targets(art.stripped_body):
            if target not in all_slugs:
                report.issues.append(
                    LintIssue(
                        check="broken_wikilink",
                        severity="error",
                        article=art.rel,
                        message=f"[[{target}]] does not resolve to any article",
                    )
                )


def _check_orphan_articles(articles: list[_LoadedArticle], report: LintReport, now: date) -> None:
    inbound: dict[str, set[str]] = defaultdict(set)
    for art in articles:
        for target in wikilink_targets(art.stripped_body):
            inbound[target].add(art.path.stem)
    for art in articles:
        created = _parse_date(art.fm.get("created", ""))
        if created and (now - created) <= timedelta(days=7):
            continue
        if not inbound.get(art.path.stem):
            report.issues.append(
                LintIssue(
                    check="orphan",
                    severity="warn",
                    article=art.rel,
                    message="no inbound wikilinks and older than 7 days",
                )
            )


def _check_stale_articles(
    articles: list[_LoadedArticle],
    report: LintReport,
    now: date,
    referenced_recent: set[str],
) -> None:
    for art in articles:
        updated = _parse_date(art.fm.get("updated", ""))
        if not updated:
            continue
        if (now - updated) <= timedelta(days=90):
            continue
        if art.path.stem in referenced_recent:
            continue
        report.issues.append(
            LintIssue(
                check="stale",
                severity="info",
                article=art.rel,
                message=(
                    f"last updated {updated.isoformat()}; no dailies reference it in last 30 days"
                ),
            )
        )


def _check_sparse_articles(articles: list[_LoadedArticle], report: LintReport) -> None:
    for art in articles:
        body_no_headings = re.sub(r"^#+\s.*$", "", art.stripped_body, flags=re.MULTILINE)
        words = len(re.findall(r"\w+", body_no_headings))
        if words < 100:
            report.issues.append(
                LintIssue(
                    check="sparse",
                    severity="info",
                    article=art.rel,
                    message=f"body has only {words} words (< 100)",
                )
            )


def _check_missing_backlinks(articles: list[_LoadedArticle], report: LintReport) -> None:
    """If A [[B]], then B's ## Backlinks should list A."""
    outgoing = {art.path.stem: wikilink_targets(art.stripped_body) for art in articles}
    declared: dict[str, set[str]] = {}
    # Stop the Backlinks capture at the next level-2 heading (or EOF) so a
    # trailing section after Backlinks doesn't get absorbed into the scan.
    backlinks_re = re.compile(
        r"^##\s+Backlinks\s*\n(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    for art in articles:
        m = backlinks_re.search(art.text)
        declared[art.path.stem] = wikilink_targets(m.group(1) if m else "")

    for src, targets in outgoing.items():
        for t in targets:
            if t not in declared:
                continue  # flagged by broken_wikilink instead
            if src not in declared[t]:
                report.issues.append(
                    LintIssue(
                        check="missing_backlink",
                        severity="info",
                        article=f"{t}.md",
                        message=f"[[{src}]] references this article but is not in its Backlinks",
                    )
                )


# ── Checks 6–7 (LLM, opt-in) ─────────────────────────────────────────────

_CONTRADICTION_SYSTEM = """\
You audit a small knowledge base for internal contradictions. You will receive
a list of article excerpts. Return a JSON array of objects:
  {"a": "<slug>", "b": "<slug>", "reason": "short explanation"}
Return [] if none. Be conservative — only flag genuine factual contradictions,
not stylistic differences.
"""

_DUPLICATE_SYSTEM = """\
You audit a small knowledge base for near-duplicate concept articles. You
will receive a list of title + first-paragraph excerpts. Return a JSON array:
  {"a": "<slug>", "b": "<slug>", "reason": "short explanation"}
Flag only articles that could be merged without loss. Return [] if none.
"""


def _llm_lint(
    *,
    system: str,
    payload: str,
    agent: AgentClient,
    cfg: Config,
    recursion_tag: RecursionTag,
) -> tuple[list[dict], float]:
    req = AgentRequest(
        system=system,
        user=payload,
        model=cfg.model_for("claude"),
        allowed_tools=[],
        max_turns=1,
        recursion_tag=recursion_tag,
    )
    resp = complete_sync(agent, req)
    raw = resp.text.strip()
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    try:
        data = json.loads(m.group(0)) if m else []
    except json.JSONDecodeError:
        data = []
    return (data if isinstance(data, list) else []), (resp.cost_usd or 0.0)


def _check_contradictions_and_duplicates(
    articles: list[_LoadedArticle],
    report: LintReport,
    *,
    agent: AgentClient,
    cfg: Config,
) -> None:
    if not articles:
        return
    items: list[str] = []
    for art in articles:
        excerpt = first_paragraph(art.stripped_body)[:400]
        items.append(
            f"- slug: {art.path.stem}\n"
            f"  title: {art.fm.get('title', art.path.stem)}\n"
            f"  excerpt: {excerpt}"
        )
    payload = "\n\n".join(items)

    dupes, c1 = _llm_lint(
        system=_DUPLICATE_SYSTEM,
        payload=payload,
        agent=agent,
        cfg=cfg,
        recursion_tag="adjoint_compile",
    )
    for d in dupes:
        report.issues.append(
            LintIssue(
                check="near_duplicate",
                severity="warn",
                article=f"{d.get('a', '?')} vs {d.get('b', '?')}",
                message=str(d.get("reason", "near-duplicate")),
            )
        )

    contra, c2 = _llm_lint(
        system=_CONTRADICTION_SYSTEM,
        payload=payload,
        agent=agent,
        cfg=cfg,
        recursion_tag="adjoint_compile",
    )
    for c in contra:
        report.issues.append(
            LintIssue(
                check="contradiction",
                severity="error",
                article=f"{c.get('a', '?')} vs {c.get('b', '?')}",
                message=str(c.get("reason", "contradiction")),
            )
        )
    report.cost_usd += c1 + c2


# ── Public API ───────────────────────────────────────────────────────────


def lint(
    *,
    project_path: Path,
    cheap: bool = False,
    config: Config | None = None,
    client: AgentClient | None = None,
    paths: UserPaths | None = None,
    now: date | None = None,
) -> LintReport:
    cfg = config or load_config(project_path)
    up = paths or user_paths()
    pp = up.project(project_path)
    pp.ensure()
    today = now or datetime.now(UTC).date()

    articles = _load_articles(pp)
    all_slugs = {art.path.stem for art in articles}
    referenced_recent = _slugs_referenced_in_recent_dailies(pp, within_days=30, now=today)

    report = LintReport(articles_scanned=len(articles))
    _check_broken_wikilinks(articles, all_slugs, report)
    _check_orphan_articles(articles, report, today)
    _check_stale_articles(articles, report, today, referenced_recent)
    _check_sparse_articles(articles, report)
    _check_missing_backlinks(articles, report)
    if not cheap:
        _check_contradictions_and_duplicates(
            articles, report, agent=client or default_client(), cfg=cfg
        )

    write_report(pp, report)
    log_event(
        get_logger("memory.lint"),
        "lint.ok",
        issues=len(report.issues),
        articles=report.articles_scanned,
        cheap=cheap,
        cost_usd=report.cost_usd,
    )
    return report


def write_report(pp: ProjectPaths, report: LintReport) -> Path:
    pp.knowledge_dir.mkdir(parents=True, exist_ok=True)
    path = pp.knowledge_dir / ".lint-report.md"
    lines = [
        "# Knowledge Base — Lint Report",
        "",
        f"- Articles scanned: **{report.articles_scanned}**",
        f"- Issues: **{len(report.issues)}**",
        f"- LLM cost: ${report.cost_usd:.4f}"
        if report.cost_usd
        else "- LLM cost: $0.00 (cheap mode)",
        "",
    ]
    by_check = report.by_check()
    for check in (
        "broken_wikilink",
        "orphan",
        "stale",
        "sparse",
        "missing_backlink",
        "near_duplicate",
        "contradiction",
    ):
        items = by_check.get(check, [])
        lines.append(f"## {check} ({len(items)})")
        if not items:
            lines.append("_none_\n")
            continue
        for i in items:
            lines.append(f"- **{i.severity}** `{i.article}` — {i.message}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
