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
import json
import re
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

__all__ = ["Item", "FetchResult", "fetch_all", "fetch_one", "parse", "USER_AGENT",
           "MAX_RESPONSE_BYTES", "MAX_DECOMPRESSED_BYTES"]

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

# ---------------------------------------------------------------- limits

# A feed is normally KBs to a few MB. Twenty megabytes over the wire already
# means the source is broken — a CDN error page, a misconfigured server, a
# domain that changed hands — not that this is an unusually large feed.
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
# gzip/deflate can expand a small compressed body by three orders of
# magnitude, so the compressed-side cap above does not bound what comes out
# the other end. Checked incrementally during decompression (see
# `_inflate_capped`) so a bomb is caught while it is still inflating, not
# after it is already sitting in memory at full size.
MAX_DECOMPRESSED_BYTES = 100 * 1024 * 1024

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

class _TooBig(Exception):
    """A response is bigger than we will hold in memory to find out how big."""

    def __init__(self, what: str, limit: int):
        self.what = what
        self.limit = limit
        super().__init__(f"{what} exceeds the {limit} byte limit")


class _ShortRead(Exception):
    """The connection closed before the body finished arriving."""

    def __init__(self, got: int, expected: int | None):
        self.got = got
        self.expected = expected
        msg = (f"connection closed early — got {got} of {expected} declared bytes"
               if expected is not None else
               f"connection closed early — got {got} bytes then it stopped")
        super().__init__(msg)


_READ_CHUNK = 65536


def _read_capped(r, limit: int) -> bytes:
    """Read a response body, refusing to read past `limit` bytes.

    Reads in fixed-size chunks and checks the running total after each one, so
    an oversized body is caught a chunk after it crosses the line rather than
    after the whole thing has already been pulled into memory to measure it.

    A short body is a failure too, not a smaller success. `HTTPResponse.read()`
    with no argument raises `IncompleteRead` when the connection closes before
    `Content-Length` bytes have arrived; giving it a size instead — which
    bounding the read requires — makes a short body come back as a silent EOF
    (CPython's own http/client.py notes this trade-off). Comparing what
    actually arrived against `Content-Length` gets that failure back, so a
    dropped connection is reported as one instead of being handed to `parse`
    as a feed that is merely malformed. Chunked responses carry no
    `Content-Length` to compare against, but a broken chunked transfer can
    still raise `IncompleteRead` directly out of `read()`, so that is caught
    here too — otherwise it would escape `fetch_one` as a raised exception,
    which is exactly what this module promises never happens.
    """
    expected = None
    content_length = r.headers.get("Content-Length")
    if content_length is not None:
        try:
            expected = int(content_length)
        except ValueError:
            expected = None

    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            chunk = r.read(_READ_CHUNK)
        except IncompleteRead as e:
            total += len(e.partial or b"")
            raise _ShortRead(total, expected if expected is not None else e.expected) from e
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise _TooBig("response body", limit)

    if expected is not None and total < expected:
        raise _ShortRead(total, expected)
    return b"".join(chunks)


def _inflate_capped(data: bytes, wbits: int, limit: int) -> bytes:
    """Decompress with the same bounded-chunk discipline as `_read_capped`,
    across every member of the stream, with `limit` enforced on their
    combined output.

    Two things a single, one-shot `zlib.decompressobj` call gets wrong:

    * gzip is a container that can hold more than one concatenated member —
      exactly what streamed or concatenated compression produces, which CDNs
      do emit. `gzip.decompress()` decodes every member; a lone
      `decompressobj` decodes only the first and silently leaves the rest in
      `unused_data`. Stopping there truncates a working feed with no error —
      the "silently an empty feed" outcome this module exists to prevent — so
      whenever a decompressor reports `eof`, whatever is left (its own
      `unused_data`, plus anything from `data` not yet handed to it) starts a
      fresh decompressor instead of ending the read.
    * Input is fed forward through `data` by advancing an index, and each
      decompress call is both input- and output-bounded (`_READ_CHUNK` of
      each). Re-assigning `unconsumed_tail` as the next "remaining" buffer —
      the previous approach — feeds the same still-mostly-full tail back in
      on every iteration; slicing it is quadratic in the number of output
      chunks once the input is a lot smaller than the output, which is
      exactly the shape of a compressible feed. Bounding the input side too
      keeps `unconsumed_tail` itself small (at most one `_READ_CHUNK`), so
      re-slicing it costs nothing.
    """
    out = bytearray()
    pos, n = 0, len(data)
    d = zlib.decompressobj(wbits)
    pending = b""

    while True:
        if not pending:
            if pos >= n:
                break
            pending = data[pos:pos + _READ_CHUNK]
            pos += len(pending)

        out.extend(d.decompress(pending, _READ_CHUNK))
        if len(out) > limit:
            raise _TooBig("decompressed body", limit)
        pending = d.unconsumed_tail

        if d.eof:
            leftover = pending + d.unused_data + data[pos:]
            pos, pending = n, b""
            if not leftover:
                break
            d = zlib.decompressobj(wbits)
            pending = leftover

    out.extend(d.flush())
    if len(out) > limit:
        raise _TooBig("decompressed body", limit)
    return bytes(out)


def _decompress(raw: bytes, encoding: str) -> bytes:
    """Undo Content-Encoding. A `_TooBig` here means genuinely too big; any
    other decompression failure is treated as "not actually compressed" and
    the bytes are returned as-is, same as before this file capped anything."""
    if encoding == "gzip":
        try:
            return _inflate_capped(raw, zlib.MAX_WBITS | 16, MAX_DECOMPRESSED_BYTES)
        except _TooBig:
            raise
        except (OSError, zlib.error):
            return raw
    if encoding == "deflate":
        try:
            return _inflate_capped(raw, zlib.MAX_WBITS, MAX_DECOMPRESSED_BYTES)
        except _TooBig:
            raise
        except zlib.error:
            try:
                return _inflate_capped(raw, -zlib.MAX_WBITS, MAX_DECOMPRESSED_BYTES)
            except _TooBig:
                raise
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
            body = _read_capped(r, MAX_RESPONSE_BYTES)
            raw = _decompress(body, (r.headers.get("Content-Encoding") or "").lower())
    except HTTPError as e:
        return FetchResult(name, url, band, False,
                           error=f"HTTP {e.code} — the feed may have moved")
    except URLError as e:
        return FetchResult(name, url, band, False,
                           error=f"could not reach it ({e.reason})")
    except _TooBig as e:
        mb = e.limit // (1024 * 1024)
        return FetchResult(name, url, band, False,
                           error=f"{e.what} is bigger than the {mb} MB limit")
    except _ShortRead as e:
        return FetchResult(name, url, band, False, error=str(e))
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
