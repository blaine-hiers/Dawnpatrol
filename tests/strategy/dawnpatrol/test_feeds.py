"""feeds — parsing the four shapes a feed actually arrives in, and failing loudly.

Nothing here touches the network. Every fixture is bytes, because the thing
worth testing is what happens to a feed's *contents*, and a test that needs
Hacker News to be up is a test that fails for reasons that are not bugs.
"""

import sys
import unittest
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
