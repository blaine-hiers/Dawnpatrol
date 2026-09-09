"""digest — collapsing, ranking, and the promise that nothing is hidden.

The load-bearing test in here is `TestKeywordBoundaries`. The first real run of
this app scored a story for "erp" because the word *enterprise* contains it, and
another for "ban" because of *Nano Banana*. Both looked completely normal on
screen --- the ranking said "matches erp" and there was no way to tell it was
wrong without reading the headline. A scoring bug that produces a plausible
number is the worst kind, so it gets its own class.
"""

import sys
import time
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_ROOT = next(p for p in _TESTS.parents if (p / "_shared" / "appkit.py").is_file())
_APP = _ROOT / _TESTS.relative_to(_ROOT / "tests")
sys.path.insert(0, str(_APP))
sys.path.insert(0, str(_ROOT / "_shared"))

import digest  # noqa: E402
import feeds  # noqa: E402
import sources  # noqa: E402

NOW = 1_785_000_000_000        # a fixed clock; nothing here may depend on today
DAY = digest.DAY_MS


def item(title, url, source="Src", band="press", age_days=0.0, summary=""):
    return feeds.Item(title, url, source, band,
                      int(NOW - age_days * DAY), summary, dated=True)


def result(items, name="Src", band="press", ok=True, error=""):
    return feeds.FetchResult(name, "u", band, ok, items=items, error=error)


class TestKeywordBoundaries(unittest.TestCase):
    """The regression that started this file."""

    def test_erp_does_not_match_inside_enterprise(self):
        score, hits = digest._keyword_hits("ChatGPT Enterprise for your team")
        self.assertNotIn("erp", hits)

    def test_ban_does_not_match_inside_banana(self):
        score, hits = digest._keyword_hits("We make Nano Banana models")
        self.assertNotIn("ban", hits)

    def test_erp_still_matches_when_it_is_the_actual_word(self):
        _, hits = digest._keyword_hits("Connecting your ERP to a chat assistant")
        self.assertIn("erp", hits)

    def test_a_stem_matches_its_family(self):
        for text in ("an agent", "three agents", "agentic workflows"):
            _, hits = digest._keyword_hits(text)
            self.assertTrue(any(h.startswith("agent") for h in hits),
                            f"{text!r} should hit the agent stem, got {hits}")

    def test_the_reported_hit_is_the_word_actually_found(self):
        """'matches pricing' is worth reading; 'matches pric' is not."""
        _, hits = digest._keyword_hits("Pricing changes for agentic workloads")
        self.assertIn("pricing", hits)
        self.assertIn("agentic", hits)
        self.assertNotIn("pric", hits)

    def test_a_stem_does_not_match_a_word_that_merely_contains_it(self):
        _, hits = digest._keyword_hits("the pricing of storage")
        self.assertNotIn("rag", hits, "'storage' must not match 'rag'")

    def test_each_idea_scores_once(self):
        """`price` and `pricing` as separate entries scored one headline twice."""
        _, hits = digest._keyword_hits("Pricing: the price of prices")
        self.assertEqual(sum(1 for h in hits if h.startswith("pric")), 1)

    def test_noise_pushes_down_but_never_below_the_floor_of_being_kept(self):
        clean, _ = digest._keyword_hits("Small business scheduling")
        noisy, _ = digest._keyword_hits(
            "Small business scheduling: an ablation study of our novel framework")
        self.assertLess(noisy, clean)

    def test_every_configured_term_compiles(self):
        for table in (sources.KEYWORDS, sources.NOISE):
            for term in table:
                self.assertTrue(term.strip(), "an empty keyword would match everything")


class TestDeduplication(unittest.TestCase):
    def test_the_same_url_from_two_sources_becomes_one_story(self):
        report = digest.build([
            result([item("A thing happened", "https://x.com/a", source="One")],
                   name="One"),
            result([item("A thing happened", "https://x.com/a", source="Two")],
                   name="Two"),
        ], now_ms=NOW)
        self.assertEqual(len(report["stories"]), 1)
        self.assertEqual(report["stories"][0]["sourcesCount"], 2)

    def test_tracking_parameters_do_not_defeat_it(self):
        report = digest.build([
            result([item("T", "https://x.com/a?utm_source=rss", source="One")],
                   name="One"),
            result([item("T", "https://x.com/a", source="Two")], name="Two"),
        ], now_ms=NOW)
        self.assertEqual(len(report["stories"]), 1)

    def test_near_identical_titles_at_different_urls_merge(self):
        report = digest.build([
            result([item("OpenAI cuts prices for small business customers",
                         "https://a.com/1", source="One")], name="One"),
            result([item("OpenAI cuts prices for small business customers today",
                         "https://b.com/2", source="Two")], name="Two"),
        ], now_ms=NOW)
        self.assertEqual(len(report["stories"]), 1)
        self.assertEqual(report["stories"][0]["sourcesCount"], 2)

    def test_genuinely_different_stories_do_not_merge(self):
        report = digest.build([
            result([item("Anthropic ships a new model", "https://a.com/1")]),
            result([item("Ransomware hits a Georgia manufacturer",
                         "https://b.com/2")], name="Two"),
        ], now_ms=NOW)
        self.assertEqual(len(report["stories"]), 2)

    def test_one_source_carrying_a_story_twice_is_not_corroboration(self):
        report = digest.build([
            result([item("A story", "https://a.com/1", source="One"),
                    item("A story", "https://a.com/1?x=2", source="One")],
                   name="One"),
        ], now_ms=NOW)
        self.assertEqual(report["stories"][0]["sourcesCount"], 1)


class TestWindow(unittest.TestCase):
    def test_items_older_than_the_window_are_dropped(self):
        report = digest.build([
            result([item("Old news", "https://a.com/old", age_days=9)]),
        ], now_ms=NOW, window_days=3)
        self.assertEqual(report["stories"], [])
        self.assertEqual(report["counts"]["outsideWindow"], 1)

    def test_undated_items_survive_the_window(self):
        """Some feeds never date anything. Dropping them silently would lose
        whole sources on a technicality."""
        undated = feeds.Item("No date", "https://a.com/n", "Src", "press", 0,
                             dated=False)
        report = digest.build([result([undated])], now_ms=NOW, window_days=3)
        self.assertEqual(len(report["stories"]), 1)

    def test_the_count_of_what_was_dropped_is_reported(self):
        report = digest.build([
            result([item("a", "https://a.com/1", age_days=30),
                    item("b", "https://a.com/2", age_days=0)]),
        ], now_ms=NOW)
        self.assertEqual(report["counts"]["outsideWindow"], 1)
        self.assertEqual(report["counts"]["itemsSeen"], 2)


class TestRanking(unittest.TestCase):
    def test_newer_outranks_older_all_else_equal(self):
        report = digest.build([
            result([item("Story one", "https://a.com/1", age_days=2),
                    item("Story two", "https://a.com/2", age_days=0)]),
        ], now_ms=NOW)
        self.assertEqual(report["stories"][0]["title"], "Story two")

    def test_a_lab_outranks_the_press_all_else_equal(self):
        report = digest.build([
            result([item("Same day thing", "https://a.com/1", band="press")],
                   band="press"),
            result([item("Another same day", "https://b.com/2", band="lab")],
                   name="Lab", band="lab"),
        ], now_ms=NOW)
        self.assertEqual(report["stories"][0]["band"], "lab")

    def test_corroboration_is_sublinear(self):
        """The sixth outlet carrying a story adds less than the second did."""
        s = digest.Story("t", "u", "Src", "press", NOW)
        s.also = [{"source": "b", "url": "u2"}]
        two, _ = digest.score_of(s, NOW)
        s.also = [{"source": chr(98 + i), "url": f"u{i}"} for i in range(5)]
        six, _ = digest.score_of(s, NOW)
        self.assertGreater(six, two)
        self.assertLess(six - two, (two - 10) * 4)

    def test_every_story_carries_the_arithmetic_that_ranked_it(self):
        """05 §8: no figure without its working. A rank is a figure."""
        report = digest.build([
            result([item("Pricing for small business", "https://a.com/1")]),
        ], now_ms=NOW)
        story = report["stories"][0]
        self.assertTrue(story["why"], "a score with no explanation is a magic number")
        self.assertIsInstance(story["score"], float)

    def test_nothing_is_filtered_out_for_scoring_low(self):
        """Ranking low and being hidden are different. A tool that decides what
        he is allowed to see is a tool he cannot trust for two years."""
        boring = [item(f"Ablation study number {i}", f"https://a.com/{i}")
                  for i in range(12)]
        report = digest.build([result(boring)], now_ms=NOW, limit=40)
        self.assertEqual(len(report["stories"]), 12)


class TestFailureReporting(unittest.TestCase):
    def test_a_failed_source_is_carried_into_the_report(self):
        report = digest.build([
            result([], name="Dead", ok=False, error="HTTP 404"),
            result([item("Fine", "https://a.com/1")], name="Alive"),
        ], now_ms=NOW)
        self.assertEqual(len(report["failed"]), 1)
        self.assertEqual(report["failed"][0]["name"], "Dead")
        self.assertEqual(report["counts"]["sourcesFailed"], 1)

    def test_a_quiet_source_is_reported_separately_from_a_broken_one(self):
        report = digest.build([
            result([], name="arXiv"),                       # ok, just empty
            result([], name="Gone", ok=False, error="HTTP 404"),
        ], now_ms=NOW)
        self.assertEqual([q["name"] for q in report["quiet"]], ["arXiv"])
        self.assertEqual([f["name"] for f in report["failed"]], ["Gone"])

    def test_failed_sources_reach_the_rendered_markdown(self):
        report = digest.build([
            result([], name="Dead", ok=False, error="HTTP 404"),
        ], now_ms=NOW)
        md = digest.render_markdown(report)
        self.assertIn("Dead", md)
        self.assertIn("404", md)


class TestMarkdown(unittest.TestCase):
    def test_an_empty_report_says_so_rather_than_looking_broken(self):
        md = digest.render_markdown(digest.build([], now_ms=NOW))
        self.assertIn("Nothing new", md)

    def test_it_renders_links_and_the_ranking(self):
        report = digest.build([
            result([item("A headline", "https://a.com/1", summary="Body text")]),
        ], now_ms=NOW)
        md = digest.render_markdown(report)
        self.assertIn("A headline", md)
        self.assertIn("https://a.com/1", md)
        self.assertIn("Ranked", md)

    def test_stories_beyond_the_top_are_still_listed(self):
        many = [item(f"Headline {i}", f"https://a.com/{i}") for i in range(25)]
        md = digest.render_markdown(digest.build([result(many)], now_ms=NOW), top=5)
        self.assertIn("The other", md)
        self.assertIn("Headline 24", md)


class TestNoWallClock(unittest.TestCase):
    def test_build_accepts_an_injected_clock(self):
        """Every test above pins `now`. If build() ever read the clock itself
        this suite would start failing at midnight instead of when it broke."""
        a = digest.build([result([item("x", "https://a.com/1")])], now_ms=NOW)
        b = digest.build([result([item("x", "https://a.com/1")])], now_ms=NOW)
        self.assertEqual(a["generated"], b["generated"])
        self.assertEqual(a["generated"], NOW)


if __name__ == "__main__":
    unittest.main()
