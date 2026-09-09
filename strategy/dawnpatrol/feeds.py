"""feeds — fetch and parse news feeds with the standard library and nothing else.

There is no feedparser here for the same reason there is no Markdown library in
`hub/`: nothing pip installs ever ships with these apps. So this handles the
four shapes a feed actually arrives in --- RSS 2.0, RSS 1.0/RDF, Atom, and JSON
Feed --- and treats anything else as a source that failed rather than as an
empty one.

That distinction is the whole point of this module. A feed that returns nothing
because the network is down, a feed that returns nothing because it moved to a
new URL two years ago, and a feed that returns nothing because it is Saturday
and arXiv does not announce at weekends are three completely different facts,
and a collector that renders all three as "no news" is lying to you every
morning. Every fetch here comes back with a reason attached.
"""

from __future__ import annotations

import concurrent.futures as futures
import gzip
import json
import re
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

__all__ = ["Item", "FetchResult", "fetch_all", "fetch_one", "parse", "USER_AGENT"]

# Identifies itself honestly as a bot with a contact, which is the convention
# publishers actually want. Not an accident and not cosmetic: CISA's firewall
# **refuses** a browser-spoofing user agent and accepts this one. Three variants
# were tried against it (August 1, 2026) and only the `(+...)` contact form got
# through.
#
# The contact is `localhost` on purpose. The obvious thing to put there is an
# email address, and the only address on this machine is at the **employer's**
# domain --- which would broadcast who he works for to twenty-seven servers
# every morning, and the standing rule is that the employer is never named
# outside internal records. `localhost` is also simply true: this is a local
# install with no public address.
USER_AGENT = "Dawnpatrol/1.0 (+http://localhost)"

ATOM = "{http://www.w3.org/2005/Atom}"
RSS1 = "{http://purl.org/rss/1.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"

_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"\s+")
_ENTITY_RX = re.compile(r"&(#x?[0-9a-fA-F]+|[a-zA-Z]+);")

_ENTITIES = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " ",
    "mdash": "—", "ndash": "–", "hellip": "…", "rsquo": "’",
    "lsquo": "‘", "ldquo": "“", "rdquo": "”", "#39": "'",
}


def _unescape(text: str) -> str:
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name.startswith("#"):
            try:
                code = int(name[2:], 16) if name[1] in "xX" else int(name[1:])
                return chr(code)
            except (ValueError, OverflowError):
                return m.group(0)
        return _ENTITIES.get(name, m.group(0))

    return _ENTITY_RX.sub(sub, text)


def clean_text(raw: str | None, limit: int = 400) -> str:
    """Feed summaries are HTML. Strip it to something a person can skim."""
    if not raw:
        return ""
    text = _WS_RX.sub(" ", _unescape(_TAG_RX.sub(" ", raw))).strip()
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        text = cut + "…"
    return text


@dataclass
class Item:
    title: str
    url: str
    source: str
    band: str
    published: int          # epoch ms, 0 when the feed did not say
    summary: str = ""
    dated: bool = True      # False when `published` is a guess, not a fact

    def to_json(self) -> dict:
        return {
            "title": self.title, "url": self.url, "source": self.source,
            "band": self.band, "published": self.published,
            "summary": self.summary, "dated": self.dated,
        }


@dataclass
class FetchResult:
    """One source's outcome. `ok` and `items` are not the same question.

    A source can be `ok=True` with zero items --- arXiv at a weekend --- and
    that must not read the same as a 404.
    """
    name: str
    url: str
    band: str
    ok: bool
    items: list[Item] = field(default_factory=list)
    error: str = ""
    note: str = ""

    def to_json(self) -> dict:
        return {"name": self.name, "url": self.url, "band": self.band,
                "ok": self.ok, "count": len(self.items),
                "error": self.error, "note": self.note}


# ---------------------------------------------------------------- dates

def _epoch_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def parse_date(raw: str | None) -> int:
    """RFC 822 (RSS) or ISO 8601 (Atom). 0 when it cannot be read.

    Returning 0 rather than "now" is deliberate: a missing date that defaults
    to now sorts an undated item straight to the top of today's report, which
    is exactly backwards.
    """
    if not raw:
        return 0
    raw = raw.strip()
    try:
        return _epoch_ms(parsedate_to_datetime(raw))
    except (TypeError, ValueError, IndexError):
        pass
    iso = raw.replace("Z", "+00:00")
    try:
        return _epoch_ms(datetime.fromisoformat(iso))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return _epoch_ms(datetime.strptime(raw, fmt))
        except ValueError:
            continue
    return 0


# ---------------------------------------------------------------- parsing

def _text(node, *paths: str) -> str:
    for p in paths:
        found = node.find(p)
        if found is not None:
            if found.text and found.text.strip():
                return found.text.strip()
            href = found.get("href")
            if href:
                return href.strip()
    return ""


def _parse_rss2(root, name: str, band: str) -> list[Item]:
    out = []
    for it in root.iter("item"):
        title = clean_text(_text(it, "title"), 300)
        link = _text(it, "link", "guid")
        if not title or not link.startswith("http"):
            continue
        when = parse_date(_text(it, "pubDate") or _text(it, f"{DC}date"))
        out.append(Item(title, link, name, band, when,
                        clean_text(_text(it, "description", f"{CONTENT}encoded")),
                        dated=when > 0))
    return out


def _parse_rdf(root, name: str, band: str) -> list[Item]:
    out = []
    for it in root.iter(f"{RSS1}item"):
        title = clean_text(_text(it, f"{RSS1}title"), 300)
        link = _text(it, f"{RSS1}link")
        if not title or not link.startswith("http"):
            continue
        when = parse_date(_text(it, f"{DC}date"))
        out.append(Item(title, link, name, band, when,
                        clean_text(_text(it, f"{RSS1}description")),
                        dated=when > 0))
    return out


def _parse_atom(root, name: str, band: str) -> list[Item]:
    out = []
    for e in root.iter(f"{ATOM}entry"):
        title = clean_text(_text(e, f"{ATOM}title"), 300)
        link = ""
        for ln in e.findall(f"{ATOM}link"):
            rel = ln.get("rel", "alternate")
            if rel == "alternate" and ln.get("href"):
                link = ln.get("href", "")
                break
        if not link:
            link = _text(e, f"{ATOM}id")
        if not title or not link.startswith("http"):
            continue
        when = parse_date(_text(e, f"{ATOM}published", f"{ATOM}updated"))
        out.append(Item(title, link, name, band, when,
                        clean_text(_text(e, f"{ATOM}summary", f"{ATOM}content")),
                        dated=when > 0))
    return out


def _parse_jsonfeed(raw: bytes, name: str, band: str) -> list[Item]:
    doc = json.loads(raw.decode("utf-8", "replace"))
    out = []
    for e in doc.get("items") or []:
        title = clean_text(e.get("title") or "", 300)
        link = e.get("url") or e.get("external_url") or ""
        if not title or not str(link).startswith("http"):
            continue
        when = parse_date(e.get("date_published") or e.get("date_modified"))
        out.append(Item(title, link, name, band, when,
                        clean_text(e.get("summary") or e.get("content_text")
                                   or e.get("content_html") or ""),
                        dated=when > 0))
    return out


def parse(raw: bytes, name: str, band: str) -> tuple[list[Item], str]:
    """(items, note). Raises ValueError when the bytes are not a feed at all."""
    if raw.lstrip()[:1] == b"{":
        return _parse_jsonfeed(raw, name, band), "json feed"
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise ValueError(f"not valid XML or JSON ({e})") from e
    tag = root.tag.split("}")[-1]
    if tag == "rss":
        return _parse_rss2(root, name, band), "rss 2.0"
    if tag == "feed":
        return _parse_atom(root, name, band), "atom"
    if tag == "RDF":
        return _parse_rdf(root, name, band), "rss 1.0"
    raise ValueError(f"unknown feed root <{tag}>")


# ---------------------------------------------------------------- fetching

def _decompress(raw: bytes, encoding: str) -> bytes:
    if encoding == "gzip":
        try:
            return gzip.decompress(raw)
        except (OSError, zlib.error):
            return raw
    if encoding == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            try:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
            except zlib.error:
                return raw
    return raw


def fetch_one(source: dict, timeout: float = 25.0) -> FetchResult:
    """Never raises. A source that fails is a reported fact, not an exception."""
    name = source.get("name", "?")
    url = source.get("url", "")
    band = source.get("band", "other")
    req = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": ("application/rss+xml, application/atom+xml, application/xml, "
                   "text/xml, application/json;q=0.9, */*;q=0.5"),
        "Accept-Encoding": "gzip, deflate",
    })
    try:
        with urlopen(req, timeout=timeout) as r:
            raw = _decompress(r.read(), (r.headers.get("Content-Encoding") or "").lower())
    except HTTPError as e:
        return FetchResult(name, url, band, False,
                           error=f"HTTP {e.code} — the feed may have moved")
    except URLError as e:
        return FetchResult(name, url, band, False,
                           error=f"could not reach it ({e.reason})")
    except (TimeoutError, OSError) as e:
        return FetchResult(name, url, band, False, error=f"{type(e).__name__}: {e}")

    try:
        items, note = parse(raw, name, band)
    except ValueError as e:
        return FetchResult(name, url, band, False, error=str(e))
    except Exception as e:                      # a malformed feed is not a crash
        return FetchResult(name, url, band, False, error=f"{type(e).__name__}: {e}")

    if not items:
        note += " — reachable, but nothing in it right now"
    return FetchResult(name, url, band, True, items=items, note=note)


def fetch_all(sources: list[dict], timeout: float = 25.0,
              workers: int = 8) -> list[FetchResult]:
    """All sources at once. Order of the input list is preserved in the output."""
    if not sources:
        return []
    with futures.ThreadPoolExecutor(max_workers=min(workers, len(sources))) as ex:
        return list(ex.map(lambda s: fetch_one(s, timeout), sources))
