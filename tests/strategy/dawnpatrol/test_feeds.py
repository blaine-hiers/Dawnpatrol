"""feeds — parsing the four shapes a feed actually arrives in, and failing loudly.

Nothing here touches the network. Every fixture is bytes, because the thing
worth testing is what happens to a feed's *contents*, and a test that needs
Hacker News to be up is a test that fails for reasons that are not bugs.
"""

import gzip
import sys
import unittest
import zlib
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_ROOT = next(p for p in _TESTS.parents if (p / "_shared" / "appkit.py").is_file())
_APP = _ROOT / _TESTS.relative_to(_ROOT / "tests")
sys.path.insert(0, str(_APP))
sys.path.insert(0, str(_ROOT / "_shared"))

import feeds  # noqa: E402


RSS2 = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Example</title>
  <item>
    <title>Pricing drops for small business</title>
    <link>https://example.com/a?utm_source=rss&amp;id=7</link>
    <pubDate>Fri, 31 Jul 2026 12:00:00 +0000</pubDate>
    <description>&lt;p&gt;Some &lt;b&gt;html&lt;/b&gt; here.&lt;/p&gt;</description>
  </item>
  <item>
    <title>No link here</title>
    <link>not-a-url</link>
  </item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>An atom entry</title>
    <link rel="alternate" href="https://example.com/atom-1"/>
    <published>2026-07-30T09:15:00Z</published>
    <summary>Short summary</summary>
  </entry>
</feed>"""

RDF = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
  <item>
    <title>An RDF item</title>
    <link>https://example.com/rdf-1</link>
    <dc:date>2026-07-29T08:00:00Z</dc:date>
    <description>Body</description>
  </item>
</rdf:RDF>"""

JSONFEED = (b'{"version":"https://jsonfeed.org/version/1.1","items":['
            b'{"title":"A json item","url":"https://example.com/j1",'
            b'"date_published":"2026-07-28T10:00:00Z","summary":"Hi"}]}')


class TestParsing(unittest.TestCase):
    def test_rss2(self):
        items, note = feeds.parse(RSS2, "Example", "press")
        self.assertEqual(note, "rss 2.0")
        self.assertEqual(len(items), 1, "the item with a non-http link is dropped")
        self.assertEqual(items[0].title, "Pricing drops for small business")
        self.assertEqual(items[0].band, "press")
        self.assertTrue(items[0].dated)

    def test_rss2_strips_html_and_entities_from_the_summary(self):
        items, _ = feeds.parse(RSS2, "Example", "press")
        self.assertEqual(items[0].summary, "Some html here.")

    def test_atom(self):
        items, note = feeds.parse(ATOM, "Example", "lab")
        self.assertEqual(note, "atom")
        self.assertEqual(items[0].url, "https://example.com/atom-1")

    def test_rdf(self):
        items, note = feeds.parse(RDF, "Example", "record")
        self.assertEqual(note, "rss 1.0")
        self.assertEqual(items[0].title, "An RDF item")

    def test_json_feed(self):
        items, note = feeds.parse(JSONFEED, "Example", "press")
        self.assertEqual(note, "json feed")
        self.assertEqual(items[0].url, "https://example.com/j1")

    def test_something_that_is_not_a_feed_raises_rather_than_returning_empty(self):
        with self.assertRaises(ValueError):
            feeds.parse(b"<html><body>not a feed</body></html>", "X", "press")
        with self.assertRaises(ValueError):
            feeds.parse(b"complete gibberish {{{", "X", "press")


class TestDates(unittest.TestCase):
    def test_rfc822(self):
        self.assertGreater(feeds.parse_date("Fri, 31 Jul 2026 12:00:00 +0000"), 0)

    def test_iso8601_with_z(self):
        self.assertGreater(feeds.parse_date("2026-07-30T09:15:00Z"), 0)

    def test_unparseable_is_zero_not_now(self):
        """An undated item defaulting to now sorts straight to the top of
        today's report, which is exactly backwards."""
        self.assertEqual(feeds.parse_date("last Tuesday-ish"), 0)
        self.assertEqual(feeds.parse_date(None), 0)
        self.assertEqual(feeds.parse_date(""), 0)

    def test_an_undated_item_is_marked_undated(self):
        raw = b"""<?xml version="1.0"?><rss version="2.0"><channel><item>
          <title>Undated</title><link>https://example.com/u</link>
        </item></channel></rss>"""
        items, _ = feeds.parse(raw, "X", "press")
        self.assertFalse(items[0].dated)
        self.assertEqual(items[0].published, 0)


class TestCleanText(unittest.TestCase):
    def test_collapses_whitespace_and_strips_tags(self):
        self.assertEqual(feeds.clean_text("<p>a   b\n\nc</p>"), "a b c")

    def test_truncates_on_a_word_boundary(self):
        out = feeds.clean_text("word " * 200, limit=50)
        self.assertLessEqual(len(out), 51)
        self.assertTrue(out.endswith("…"))

    def test_handles_none(self):
        self.assertEqual(feeds.clean_text(None), "")


class TestFetchResult(unittest.TestCase):
    def test_reachable_but_empty_is_not_the_same_as_failed(self):
        """arXiv at a weekend and a 404 must never render the same way."""
        quiet = feeds.FetchResult("arXiv", "u", "record", True, items=[], note="rss 2.0")
        dead = feeds.FetchResult("Gone", "u", "lab", False, error="HTTP 404")
        self.assertTrue(quiet.ok)
        self.assertFalse(dead.ok)
        self.assertEqual(quiet.to_json()["count"], 0)
        self.assertEqual(dead.to_json()["error"], "HTTP 404")

    def test_fetch_one_never_raises_on_a_dead_host(self):
        res = feeds.fetch_one(
            {"name": "Nope", "band": "press",
             "url": "http://127.0.0.1:9/definitely-not-listening"},
            timeout=3,
        )
        self.assertFalse(res.ok)
        self.assertTrue(res.error)

    def test_fetch_all_of_nothing_is_empty_not_an_error(self):
        self.assertEqual(feeds.fetch_all([]), [])


class _FakeHeaders:
    def __init__(self, d: dict | None = None):
        self._d = d or {}

    def get(self, key, default=None):
        return self._d.get(key, default)


class _FakeResponse:
    """Just enough of an `http.client.HTTPResponse` for `fetch_one` to use:
    a chunked `.read(n)`, headers, and a no-op context manager."""

    def __init__(self, data: bytes, headers: dict | None = None):
        self._data = data
        self._pos = 0
        self.headers = _FakeHeaders(headers)

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk, self._pos = self._data[self._pos:], len(self._data)
            return chunk
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestSizeCeilings(unittest.TestCase):
    """A source that returns too much, or that inflates into too much, comes
    back as `FetchResult(ok=False)` — never a raised exception and never a
    silent empty feed."""

    def setUp(self):
        self._orig_urlopen = feeds.urlopen
        self._orig_max_response = feeds.MAX_RESPONSE_BYTES
        self._orig_max_decompressed = feeds.MAX_DECOMPRESSED_BYTES

    def tearDown(self):
        feeds.urlopen = self._orig_urlopen
        feeds.MAX_RESPONSE_BYTES = self._orig_max_response
        feeds.MAX_DECOMPRESSED_BYTES = self._orig_max_decompressed

    def _serve(self, data: bytes, headers: dict | None = None):
        feeds.urlopen = lambda req, timeout=None: _FakeResponse(data, headers)

    def test_oversize_body_is_reported_not_read_in_full(self):
        feeds.MAX_RESPONSE_BYTES = 2 * 1024 * 1024
        self._serve(b"x" * (3 * 1024 * 1024))
        res = feeds.fetch_one({"name": "Big", "band": "press",
                                "url": "http://example.test/feed"})
        self.assertFalse(res.ok)
        self.assertEqual(res.items, [])
        self.assertIn("response body", res.error)
        self.assertIn("2 MB", res.error)

    def test_small_compressed_body_that_decompresses_oversize_is_reported(self):
        # Five megabytes of one repeated byte compresses to almost nothing,
        # which is exactly the shape of a decompression bomb: it sails past
        # the compressed-side ceiling with room to spare.
        plain = b"a" * (5 * 1024 * 1024)
        compressed = gzip.compress(plain)
        self.assertLess(len(compressed), 1024 * 1024,
                        "fixture should be small compressed to be a real test")

        feeds.MAX_RESPONSE_BYTES = 10 * 1024 * 1024
        feeds.MAX_DECOMPRESSED_BYTES = 2 * 1024 * 1024
        self._serve(compressed, {"Content-Encoding": "gzip"})
        res = feeds.fetch_one({"name": "Bomb", "band": "press",
                                "url": "http://example.test/feed"})
        self.assertFalse(res.ok)
        self.assertIn("decompressed body", res.error)
        self.assertIn("2 MB", res.error)

    def test_a_body_within_both_limits_still_works(self):
        plain = b'{"version": "https://jsonfeed.org/version/1.1", "items": []}'
        self._serve(gzip.compress(plain), {"Content-Encoding": "gzip"})
        res = feeds.fetch_one({"name": "Fine", "band": "press",
                                "url": "http://example.test/feed"})
        self.assertTrue(res.ok)

    def test_a_body_that_is_not_actually_compressed_falls_back_to_the_raw_bytes(self):
        """Content-Encoding can lie. A genuine decompression failure — as
        opposed to a `_TooBig` — means "not actually compressed", not "this
        fetch failed"; the raw bytes still get a chance to parse."""
        self._serve(RSS2, {"Content-Encoding": "gzip"})
        res = feeds.fetch_one({"name": "Mislabeled", "band": "press",
                                "url": "http://example.test/feed"})
        self.assertTrue(res.ok)
        self.assertEqual(len(res.items), 1)

    def test_multi_member_gzip_decodes_every_member_through_fetch_one(self):
        """A CDN can emit concatenated gzip members from streamed
        compression. `gzip.decompress` reads all of them; a single
        `zlib.decompressobj` reads only the first and leaves the rest in
        `unused_data` — silently truncating a working feed is exactly the
        "silently an empty feed" outcome this module must never produce."""
        first_half = RSS2[:len(RSS2) // 2]
        second_half = RSS2[len(RSS2) // 2:]
        multi_member = gzip.compress(first_half) + gzip.compress(second_half)
        # Sanity: the fixture really is multi-member, and gzip.decompress
        # really does need both members to recover the original feed.
        self.assertEqual(gzip.decompress(multi_member), RSS2)

        self._serve(multi_member, {"Content-Encoding": "gzip"})
        res = feeds.fetch_one({"name": "Streamed", "band": "press",
                                "url": "http://example.test/feed"})
        self.assertTrue(res.ok, res.error)
        self.assertEqual(len(res.items), 1)

    def test_a_body_shorter_than_content_length_is_reported_not_silently_truncated(self):
        """Passing a size to `read()` — which bounding the read requires —
        turns a short body into a silent EOF instead of the `IncompleteRead`
        that `read()` with no argument would raise. A dropped connection must
        not be handed to `parse` as if it were merely a malformed feed."""
        self._serve(b"short", {"Content-Length": "500"})
        res = feeds.fetch_one({"name": "Cut", "band": "press",
                                "url": "http://example.test/feed"})
        self.assertFalse(res.ok)
        self.assertIn("closed early", res.error)
        self.assertIn("500", res.error)

    def test_trailing_zero_padding_after_a_gzip_member_still_parses(self):
        """Some encoders pad a gzip response with trailing NUL bytes;
        `gzip.decompress` tolerates this and so must `_inflate_capped` —
        the old one-shot path decoded this feed fine, and a decompression
        rewrite must not turn it into a reported failure (issue #15)."""
        padded = gzip.compress(RSS2) + b"\x00" * 16
        self._serve(padded, {"Content-Encoding": "gzip"})
        res = feeds.fetch_one({"name": "Padded", "band": "press",
                                "url": "http://example.test/feed"})
        self.assertTrue(res.ok, res.error)
        self.assertEqual(len(res.items), 1)

    def test_zero_padding_between_members_still_parses_through_fetch_one(self):
        first_half = RSS2[:len(RSS2) // 2]
        second_half = RSS2[len(RSS2) // 2:]
        mixed = gzip.compress(first_half) + b"\x00" * 8 + gzip.compress(second_half)
        self._serve(mixed, {"Content-Encoding": "gzip"})
        res = feeds.fetch_one({"name": "PaddedMiddle", "band": "press",
                                "url": "http://example.test/feed"})
        self.assertTrue(res.ok, res.error)
        self.assertEqual(len(res.items), 1)

    def test_a_truncated_gzip_stream_is_reported_not_partially_parsed(self):
        """Cutting a gzip stream short of its final member must not hand
        `parse` a partial document that merely happens to fail to parse —
        it is reported as the fetch failure it actually is, matching what
        the old one-shot `gzip.decompress` (which raised `EOFError`) did.
        No `Content-Length` header here, on purpose: this exercises the
        decompression-level detection, not `_read_capped`'s short-body
        check, which would otherwise catch this first."""
        truncated = gzip.compress(RSS2)[:-5]
        self._serve(truncated, {"Content-Encoding": "gzip"})
        res = feeds.fetch_one({"name": "Truncated", "band": "press",
                                "url": "http://example.test/feed"})
        self.assertFalse(res.ok)

    def test_cap_message_uses_a_sensible_unit_below_one_megabyte(self):
        """`limit // (1024*1024)` reads "0 MB" for any cap under 1 MB, which
        describes no limit at all."""
        feeds.MAX_RESPONSE_BYTES = 1000
        self._serve(b"x" * 2000)
        res = feeds.fetch_one({"name": "Small", "band": "press",
                                "url": "http://example.test/feed"})
        self.assertFalse(res.ok)
        self.assertIn("1000 bytes", res.error)
        self.assertNotIn("0 MB", res.error)


class TestInflateCapped(unittest.TestCase):
    """Direct coverage of `_inflate_capped`'s multi-member handling, separate
    from the network path."""

    def test_multi_member_gzip_matches_gzip_decompress(self):
        first_half = RSS2[:len(RSS2) // 2]
        second_half = RSS2[len(RSS2) // 2:]
        multi_member = gzip.compress(first_half) + gzip.compress(second_half)
        expected = gzip.decompress(multi_member)
        got = feeds._inflate_capped(multi_member, zlib.MAX_WBITS | 16, 10**9)
        self.assertEqual(got, expected)
        self.assertEqual(got, RSS2)

    def test_a_single_member_still_works(self):
        got = feeds._inflate_capped(gzip.compress(RSS2), zlib.MAX_WBITS | 16, 10**9)
        self.assertEqual(got, RSS2)

    def test_trailing_zero_padding_is_discarded_matching_gzip_decompress(self):
        padded = gzip.compress(RSS2) + b"\x00" * 16
        self.assertEqual(gzip.decompress(padded), RSS2)   # the fixture is real
        got = feeds._inflate_capped(padded, zlib.MAX_WBITS | 16, 10**9)
        self.assertEqual(got, RSS2)

    def test_zero_padding_between_two_members_still_decodes_both(self):
        """Padding is not only tolerated at the very end --
        `gzip.compress(A) + zero-padding + gzip.compress(B)` is a real (if
        unusual) two-member stream, and the padding *between* members must
        be discarded the same way padding *after* the last one is. This
        used to raise `zlib.error` because only a wholly-zero leftover was
        recognised as padding, so a real member following some zero bytes
        was fed to a fresh decompressor with the leading zeros still stuck
        to the front of it."""
        first_half = RSS2[:len(RSS2) // 2]
        second_half = RSS2[len(RSS2) // 2:]
        mixed = gzip.compress(first_half) + b"\x00" * 8 + gzip.compress(second_half)
        expected = gzip.decompress(mixed)
        self.assertEqual(expected, RSS2)          # the fixture is real
        got = feeds._inflate_capped(mixed, zlib.MAX_WBITS | 16, 10**9)
        self.assertEqual(got, expected)

    def test_the_size_cap_still_applies_to_a_member_that_follows_padding(self):
        """Skipping the inert padding bytes must not also skip the cap
        check on the real member that follows them."""
        small = gzip.compress(b"a")
        bomb = gzip.compress(b"a" * (2 * 1024 * 1024))
        mixed = small + b"\x00" * 8 + bomb
        with self.assertRaises(feeds._TooBig):
            feeds._inflate_capped(mixed, zlib.MAX_WBITS | 16, 1024 * 1024)

    def test_trailing_non_zero_junk_still_raises(self):
        """Padding is forgiven; corruption is not. `\\x01` bytes can never be
        a real member's magic, so this is not "another member" either --
        it is the same failure a one-shot decompress would report."""
        junk = gzip.compress(RSS2) + b"\x01" * 16
        with self.assertRaises(zlib.error):
            feeds._inflate_capped(junk, zlib.MAX_WBITS | 16, 10**9)

    def test_a_truncated_stream_raises_eoferror(self):
        truncated = gzip.compress(RSS2)[:-5]
        with self.assertRaises(EOFError):
            feeds._inflate_capped(truncated, zlib.MAX_WBITS | 16, 10**9)

    def test_empty_input_is_empty_output_not_an_error(self):
        """Matches `gzip.decompress(b"") == b""` -- empty is not truncated."""
        self.assertEqual(feeds._inflate_capped(b"", zlib.MAX_WBITS | 16, 10**9), b"")


class TestFmtBytes(unittest.TestCase):
    def test_at_least_a_megabyte_is_reported_in_mb(self):
        self.assertEqual(feeds._fmt_bytes(20 * 1024 * 1024), "20 MB")

    def test_below_a_megabyte_is_reported_in_kb(self):
        self.assertEqual(feeds._fmt_bytes(2048), "2 KB")

    def test_below_a_kilobyte_is_reported_in_bytes(self):
        self.assertEqual(feeds._fmt_bytes(500), "500 bytes")
        self.assertNotIn("0 MB", feeds._fmt_bytes(500))


class TestUserAgent(unittest.TestCase):
    def test_the_user_agent_carries_no_private_identity(self):
        """A user agent goes to twenty-seven servers a day. Whoever runs this
        has an email address and probably an employer, and neither belongs in a
        header sent to every publisher on the list. The contact form below is
        the only identity a feed is owed."""
        self.assertNotIn("@", feeds.USER_AGENT)
        for private in ("gmail", "outlook", "yahoo", "hotmail", ".com/~"):
            self.assertNotIn(private, feeds.USER_AGENT.lower())

    def test_it_identifies_itself_with_a_contact(self):
        """Not cosmetic: CISA's firewall refuses a browser-spoofing agent and
        accepts the `(+...)` contact form."""
        self.assertIn("(+", feeds.USER_AGENT)


if __name__ == "__main__":
    unittest.main()
