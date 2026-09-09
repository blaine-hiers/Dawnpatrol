"""digest — turn a pile of feed items into something worth four minutes.

Three jobs, in order:

  1. **Collapse duplicates.** One announcement covered by six outlets is one
     item with six links, not six items. Corroboration is then evidence rather
     than repetition --- a story six places carried is a story that happened.
  2. **Rank.** Recency, the band it came from, how many independent sources
     carried it, and how much it has to do with advising a 20-person company.
  3. **Render.** A dated report with the arithmetic of its own ranking visible,
     because `05` Section 8 forbids a number without its working and there is
     no good reason for this app to be the exception.

What it deliberately does not do is *filter*. Everything collected is kept and
ordered; nothing is dropped for scoring low. A tool that silently hides what it
judged uninteresting is a tool that quietly narrows what he knows, and he would
have no way of finding out.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import sources as src

__all__ = ["Story", "build", "render_markdown", "score_of", "normalise_title"]

DAY_MS = 86_400_000

_PUNCT_RX = re.compile(r"[^a-z0-9 ]+")
_WS_RX = re.compile(r"\s+")

# Words too common in this subject to help tell two stories apart.
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is",
    "are", "at", "by", "from", "as", "its", "it", "this", "that", "new", "now",
    "ai", "llm", "model", "models", "using", "how", "why", "what", "can",
}


def normalise_title(title: str) -> str:
    return _WS_RX.sub(" ", _PUNCT_RX.sub(" ", title.lower())).strip()


def _shingles(title: str) -> set[str]:
    words = [w for w in normalise_title(title).split() if w not in _STOP and len(w) > 2]
    return set(words)


def _canonical_url(url: str) -> str:
    """Drop tracking noise so two links to one article compare equal."""
    url = url.split("#")[0]
    if "?" in url:
        base, _, query = url.partition("?")
        keep = [p for p in query.split("&")
                if p and not p.split("=")[0].lower().startswith(
                    ("utm_", "ref", "source", "fbclid", "gclid", "mc_"))]
        url = base + ("?" + "&".join(keep) if keep else "")
    return url.rstrip("/").lower()


@dataclass
class Story:
    title: str
    url: str
    source: str
    band: str
    published: int
    summary: str = ""
    dated: bool = True
    also: list[dict] = field(default_factory=list)   # corroborating coverage
    score: float = 0.0
    why: list[str] = field(default_factory=list)     # the visible arithmetic

    @property
    def sources_count(self) -> int:
        return 1 + len(self.also)

    def to_json(self) -> dict:
        return {
            "title": self.title, "url": self.url, "source": self.source,
            "band": self.band, "published": self.published,
            "summary": self.summary, "dated": self.dated, "also": self.also,
            "score": round(self.score, 2), "why": self.why,
            "sourcesCount": self.sources_count,
        }


# ---------------------------------------------------------------- scoring

def _compile_terms(table: dict[str, float]) -> list[tuple[str, re.Pattern, float]]:
    """Word-boundary patterns, not substrings.

    Plain `"erp" in text` matched ent**erp**rise, and `"ban"` matched Nano
    **Ban**ana --- both seen in the first real run, both scoring a story on a
    word that was never in it. A ranking that cannot be trusted is worse than
    no ranking, because it still looks like one.

    A trailing `*` marks a stem: `deprecat*` catches deprecate, deprecated and
    deprecation without also catching a word that merely contains it.
    """
    out = []
    for term, weight in table.items():
        stem = term.endswith("*")
        body = term[:-1] if stem else term
        # A stem consumes the rest of the word (`\w*`) rather than stopping at
        # its own last letter. Without that the pattern still *matches*
        # correctly, but `group(0)` is "pric" instead of "pricing" -- and the
        # matched text is what gets shown to the reader.
        pattern = r"\b" + re.escape(body) + (r"\w*" if stem else r"\b")
        out.append((body, re.compile(pattern, re.IGNORECASE), weight))
    return out


_KEYWORD_TERMS = _compile_terms(src.KEYWORDS)
_NOISE_TERMS = _compile_terms(src.NOISE)


def _keyword_hits(text: str) -> tuple[float, list[str]]:
    """(score, the words actually found).

    The reported hit is the **matched word**, not the pattern --- "matches
    pricing" is worth reading and "matches pric" is not. It also keeps the
    explanation honest: what the reader is shown is literally what was in the
    headline.
    """
    total, hits = 0.0, []
    for _, rx, weight in _KEYWORD_TERMS:
        found = rx.search(text)
        if found:
            total += weight
            word = found.group(0).lower()
            if word not in hits:
                hits.append(word)
    for _, rx, weight in _NOISE_TERMS:
        if rx.search(text):
            total += weight
    return total, hits


def score_of(story: Story, now_ms: int) -> tuple[float, list[str]]:
    """Returns (score, why) --- `why` is shown to the reader, not just logged."""
    why: list[str] = []

    # Recency. Full marks for today, decaying over a week, never negative.
    age_days = max(0.0, (now_ms - story.published) / DAY_MS) if story.published else 3.0
    recency = max(0.0, 10.0 - (age_days * 1.6))
    if not story.dated:
        recency = 3.0
        why.append("no date in the feed — treated as mid-week")
    elif age_days < 1:
        why.append("today")
    elif age_days < 2:
        why.append("yesterday")
    else:
        why.append(f"{int(age_days)} days old")

    band = src.BANDS.get(story.band, {})
    band_weight = float(band.get("weight", 1.0))
    if band_weight != 1.0:
        why.append(f"{band.get('title', story.band).lower()} ×{band_weight:g}")

    # Corroboration. Sub-linear: the sixth outlet carrying a story adds less
    # than the second did, because the second is what made it real.
    extra = story.sources_count - 1
    corroboration = 0.0
    if extra:
        corroboration = 4.0 * (extra ** 0.6)
        why.append(f"carried by {story.sources_count} sources (+{corroboration:.1f})")

    relevance, hits = _keyword_hits(story.title + " " + story.summary)
    if hits:
        shown = ", ".join(sorted(hits)[:4])
        why.append(f"matches {shown}" + ("…" if len(hits) > 4 else ""))

    score = (recency + corroboration + relevance) * band_weight
    return score, why


# ---------------------------------------------------------------- building

def build(results, now_ms: int | None = None, window_days: int = 3,
          limit: int = 40) -> dict:
    """Collapse, score and order. `results` is a list of feeds.FetchResult.

    `window_days` keeps the archive feeds honest: OpenAI's RSS carries 1,105
    entries going back years, and without a window the "news" would be whatever
    happened to sort first.
    """
    now_ms = now_ms or int(time.time() * 1000)
    cutoff = now_ms - (window_days * DAY_MS)

    by_url: dict[str, Story] = {}
    considered = 0
    too_old = 0

    for res in results:
        if not res.ok:
            continue
        for item in res.items:
            considered += 1
            # An undated item is kept --- some feeds simply never date --- but
            # a dated item older than the window is not news.
            if item.dated and item.published < cutoff:
                too_old += 1
                continue
            key = _canonical_url(item.url)
            if key in by_url:
                existing = by_url[key]
                if item.source != existing.source:
                    existing.also.append({"source": item.source, "url": item.url})
                continue
            by_url[key] = Story(
                title=item.title, url=item.url, source=item.source,
                band=item.band, published=item.published,
                summary=item.summary, dated=item.dated,
            )

    stories = list(by_url.values())

    # Second pass: same story, different URL. Compared by word overlap, and
    # only for titles with enough distinctive words left to be worth comparing.
    merged: list[Story] = []
    for story in sorted(stories, key=lambda s: -len(s.title)):
        shingle = _shingles(story.title)
        hit = None
        if len(shingle) >= 4:
            for kept in merged:
                other = _shingles(kept.title)
                if len(other) < 4:
                    continue
                overlap = len(shingle & other) / max(1, len(shingle | other))
                if overlap >= 0.6:
                    hit = kept
                    break
        if hit is not None:
            if story.source != hit.source and \
                    all(a["source"] != story.source for a in hit.also):
                hit.also.append({"source": story.source, "url": story.url})
            if story.published > hit.published:
                hit.published = story.published
        else:
            merged.append(story)

    for story in merged:
        story.score, story.why = score_of(story, now_ms)
    merged.sort(key=lambda s: (-s.score, -s.published))

    failed = [r.to_json() for r in results if not r.ok]
    quiet = [r.to_json() for r in results if r.ok and not r.items]

    return {
        "generated": now_ms,
        "windowDays": window_days,
        "stories": [s.to_json() for s in merged[:limit]],
        "counts": {
            "sourcesTried": len(results),
            "sourcesOk": sum(1 for r in results if r.ok),
            "sourcesFailed": len(failed),
            "sourcesQuiet": len(quiet),
            "itemsSeen": considered,
            "outsideWindow": too_old,
            "storiesAfterMerge": len(merged),
            "shown": min(limit, len(merged)),
        },
        "failed": failed,
        "quiet": quiet,
    }


# ---------------------------------------------------------------- rendering

def _stamp(ms: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000)) if ms else "undated"


def render_markdown(report: dict, top: int = 15) -> str:
    """The report as plain markdown --- readable in Obsidian, or in a terminal."""
    gen = report.get("generated", 0)
    counts = report.get("counts", {})
    day = time.strftime("%A %d %B %Y", time.localtime(gen / 1000)) if gen else "?"

    out: list[str] = []
    out.append(f"# AI radar — {day}")
    out.append("")
    out.append(
        f"{counts.get('sourcesOk', 0)} of {counts.get('sourcesTried', 0)} sources "
        f"answered. {counts.get('itemsSeen', 0)} items seen, "
        f"{counts.get('storiesAfterMerge', 0)} distinct stories in the last "
        f"{report.get('windowDays', 3)} days."
    )
    out.append("")

    stories = report.get("stories", [])
    if not stories:
        out.append("**Nothing new in the window.** That is a real answer, not a "
                   "failure — check the source list below before assuming it broke.")
        out.append("")
    else:
        out.append(f"## The top {min(top, len(stories))}")
        out.append("")
        for i, s in enumerate(stories[:top], 1):
            out.append(f"### {i}. {s['title']}")
            out.append("")
            out.append(f"[{s['source']}]({s['url']}) · {_stamp(s['published'])}")
            if s.get("also"):
                links = ", ".join(f"[{a['source']}]({a['url']})" for a in s["also"])
                out.append(f"Also: {links}")
            if s.get("summary"):
                out.append("")
                out.append(s["summary"])
            out.append("")
            out.append(f"*Ranked {s['score']:g} — {'; '.join(s.get('why', []))}*")
            out.append("")

        if len(stories) > top:
            out.append(f"## The other {len(stories) - top}")
            out.append("")
            for s in stories[top:]:
                also = f" (+{len(s['also'])} more)" if s.get("also") else ""
                out.append(f"- [{s['title']}]({s['url']}) — {s['source']}{also}")
            out.append("")

    failed = report.get("failed", [])
    quiet = report.get("quiet", [])
    if failed or quiet:
        out.append("## What did not answer")
        out.append("")
        out.append("Listed rather than skipped — a source that quietly stopped "
                   "working looks exactly like a quiet week.")
        out.append("")
        for f in failed:
            out.append(f"- **{f['name']}** — {f['error']}")
        for q in quiet:
            out.append(f"- {q['name']} — reachable, nothing in it")
        out.append("")

    out.append("---")
    out.append("")
    out.append("*Collected by `system/apps/strategy/dawnpatrol/`. Ranking favours "
               "what an owner-led company of 10–50 people would act on, which is "
               "not the same as what is most important in AI.*")
    return "\n".join(out)
