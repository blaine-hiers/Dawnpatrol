"""Tests for app.py — every endpoint over real HTTP, against a throwaway database.

Nothing here reaches the network. `collect()` is replaced with a stub, because
the endpoints' job is storing and serving a report, not fetching one, and a
test that needs twenty-seven websites to be up fails for reasons that are not
bugs.
"""

import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_ROOT = next(p for p in _TESTS.parents if (p / "_shared" / "appkit.py").is_file())
_APP = _ROOT / _TESTS.relative_to(_ROOT / "tests")
sys.path.insert(0, str(_APP))
sys.path.insert(0, str(_ROOT / "_shared"))

import app as appmod     # noqa: E402
import digest            # noqa: E402
import feeds             # noqa: E402
from store import Store  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="dawnpatrol-"))
appmod.store = Store(TMP / "radar.db")

BASE = None
SERVER = None


def fake_report(day="2026-08-01", titles=("A story",)):
    items = [feeds.Item(t, f"https://example.com/{i}", "Src", "press",
                        1_785_000_000_000, "summary", True)
             for i, t in enumerate(titles)]
    report = digest.build([feeds.FetchResult("Src", "u", "press", True, items=items)],
                          now_ms=1_785_000_000_000)
    report["id"] = day
    report["summary"] = ""
    report["summaryError"] = ""
    return report


def setUpModule():
    global BASE, SERVER
    # Never let a test reach the internet.
    appmod.collect = lambda window_days=3, with_summary=True: (
        appmod.store.save(appmod.REPORTS, fake_report()))
    SERVER, BASE = appmod.app.serve_background()


def tearDownModule():
    if SERVER:
        SERVER.shutdown()
        SERVER.server_close()
    shutil.rmtree(TMP, ignore_errors=True)


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class TestState(unittest.TestCase):
    def test_state_works_before_anything_has_been_collected(self):
        appmod.store.clear(appmod.REPORTS)
        status, data = call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIsNone(data["report"])
        self.assertTrue(data["staleness"]["stale"])
        self.assertIn("Nothing collected yet", data["staleness"]["line"])

    def test_state_carries_the_bands_and_the_dead_feed_list(self):
        status, data = call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIn("lab", data["bands"])
        self.assertGreater(data["sourceCount"], 20)
        self.assertTrue(any("Anthropic" in d["name"] for d in data["deadFeeds"]),
                        "the dead feeds are shown, not quietly pruned")

    def test_state_carries_a_health_row_for_every_source(self):
        status, data = call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["health"]), data["sourceCount"])
        self.assertEqual(data["healthStreakDays"], appmod.HEALTH_STREAK_DAYS)


def rpt(report_id, failed=(), quiet=(), ok=()):
    """A minimal stored report --- just the fields source_health() reads.

    `failed`/`quiet`/`ok` are `(name, error)` / `name` / `name` pairs. A name
    that appears in none of the three is deliberately *not* the same as
    `ok=[name]` --- that is exactly the "absent" case (the source was not
    part of that day's fetch at all), which real reports since digest.py's
    fix distinguish explicitly rather than leaving as an implicit default.
    """
    return {
        "id": report_id,
        "failed": [{"name": n, "error": e} for n, e in failed],
        "quiet": [{"name": n} for n in quiet],
        "ok": [{"name": n} for n in ok],
    }


class TestSourceHealth(unittest.TestCase):
    """The rollup: computed from stored reports, not from today's fetch alone.

    Failing and quiet streaks must never blur together --- feeds.py's own
    docstring is explicit that a 429 and an empty Saturday are different
    facts, and this rollup exists specifically not to flatten that.
    """

    def test_a_clean_source_has_no_streaks(self):
        reports = [rpt("2026-09-13", ok=["Good"]),
                   rpt("2026-09-14", ok=["Good"]),
                   rpt("2026-09-15", ok=["Good"])]
        [row] = appmod.source_health(reports, ["Good"])
        self.assertEqual(row["failingStreak"], 0)
        self.assertEqual(row["quietStreak"], 0)
        self.assertFalse(row["flaggedFailing"])
        self.assertFalse(row["flaggedQuiet"])
        self.assertEqual(row["lastItemDate"], "2026-09-15")
        self.assertEqual(row["sampleSize"], 3)

    def test_a_source_failing_three_running_is_flagged_with_its_latest_error(self):
        reports = [
            rpt("2026-09-15", failed=[("Bad", "HTTP 429")]),
            rpt("2026-09-14", failed=[("Bad", "HTTP 429")]),
            rpt("2026-09-13", failed=[("Bad", "HTTP 500")]),
        ]
        [row] = appmod.source_health(reports, ["Bad"])
        self.assertEqual(row["failingStreak"], 3)
        self.assertTrue(row["flaggedFailing"])
        self.assertEqual(row["lastError"], "HTTP 429")
        self.assertEqual(row["sampleSize"], 3)

    def test_a_source_quiet_three_running_is_flagged_distinctly_from_failing(self):
        reports = [
            rpt("2026-09-15", quiet=["Slow"]),
            rpt("2026-09-14", quiet=["Slow"]),
            rpt("2026-09-13", quiet=["Slow"]),
        ]
        [row] = appmod.source_health(reports, ["Slow"])
        self.assertEqual(row["quietStreak"], 3)
        self.assertTrue(row["flaggedQuiet"])
        self.assertEqual(row["failingStreak"], 0)
        self.assertFalse(row["flaggedFailing"])
        self.assertIsNone(row["lastItemDate"])

    def test_a_source_that_recovered_has_its_streak_reset(self):
        """Newest-first: today answered fine, so the failing streak is 0 even
        though the source failed for days before that --- a recovered source
        must not still read as broken."""
        reports = [
            rpt("2026-09-15", ok=["Flaky"]),                       # ok today
            rpt("2026-09-14", failed=[("Flaky", "HTTP 503")]),
            rpt("2026-09-13", failed=[("Flaky", "HTTP 503")]),
            rpt("2026-09-12", failed=[("Flaky", "HTTP 503")]),
        ]
        [row] = appmod.source_health(reports, ["Flaky"])
        self.assertEqual(row["failingStreak"], 0)
        self.assertFalse(row["flaggedFailing"])
        self.assertEqual(row["lastItemDate"], "2026-09-15")

    def test_fewer_stored_reports_than_the_threshold_says_so(self):
        """Two failing reports is all the history there is --- it must read as
        a 2-report streak out of a 2-report sample, not as a confirmed 3-day
        pattern it cannot actually support."""
        reports = [
            rpt("2026-09-15", failed=[("New", "HTTP 404")]),
            rpt("2026-09-14", failed=[("New", "HTTP 404")]),
        ]
        [row] = appmod.source_health(reports, ["New"], threshold=3)
        self.assertEqual(row["failingStreak"], 2)
        self.assertEqual(row["sampleSize"], 2)
        self.assertFalse(row["flaggedFailing"],
                         "2 failing days out of 2 stored reports is not yet 3 running")

    def test_names_are_returned_in_the_order_given_regardless_of_flags(self):
        reports = [rpt("2026-09-15")]
        rows = appmod.source_health(reports, ["A", "B", "C"])
        self.assertEqual([r["name"] for r in rows], ["A", "B", "C"])

    def test_a_source_absent_before_it_existed_never_fabricates_a_last_item_date(self):
        """The regression this rollup shipped with: a source added to
        sources.py two days ago is absent from every older report, not
        quietly "ok" in them. Absence must never be read as the source
        having answered with items on a day it was never even fetched."""
        reports = [
            rpt("2026-09-15", failed=[("New", "HTTP 404")]),
            rpt("2026-09-14", failed=[("New", "HTTP 404")]),
            rpt("2026-09-13"), rpt("2026-09-12"), rpt("2026-09-11"),
        ]
        [row] = appmod.source_health(reports, ["New"], threshold=3)
        self.assertIsNone(row["lastItemDate"],
                          "New has never once answered with an item")
        self.assertEqual(row["failingStreak"], 2)
        self.assertEqual(row["sampleSize"], 2,
                         "only the two reports New actually appears in count")
        self.assertFalse(row["flaggedFailing"])

    def test_a_source_absent_from_every_retained_report_is_all_zero_not_ok(self):
        reports = [rpt("2026-09-15"), rpt("2026-09-14"), rpt("2026-09-13")]
        [row] = appmod.source_health(reports, ["NeverAdded"])
        self.assertEqual(row["failingStreak"], 0)
        self.assertEqual(row["quietStreak"], 0)
        self.assertIsNone(row["lastItemDate"])
        self.assertEqual(row["sampleSize"], 0)
        self.assertFalse(row["flaggedFailing"])
        self.assertFalse(row["flaggedQuiet"])

    def test_an_absence_gap_neither_extends_nor_breaks_a_failing_streak(self):
        """A day the source was not fetched is not evidence it failed again
        (it cannot extend the streak) and not evidence it recovered either
        (it cannot break the streak) --- it is simply not counted."""
        reports = [
            rpt("2026-09-15", failed=[("Gappy", "HTTP 500")]),
            rpt("2026-09-14"),                                    # absent
            rpt("2026-09-13", failed=[("Gappy", "HTTP 500")]),
        ]
        [row] = appmod.source_health(reports, ["Gappy"])
        self.assertEqual(row["failingStreak"], 2)
        self.assertEqual(row["sampleSize"], 2)


class TestCollect(unittest.TestCase):
    def test_collect_stores_a_report_and_returns_it(self):
        appmod.store.clear(appmod.REPORTS)
        status, data = call("POST", "/api/collect", {})
        self.assertEqual(status, 200)
        self.assertEqual(data["report"]["id"], "2026-08-01")
        self.assertEqual(len(data["history"]), 1)

    def test_collecting_twice_in_a_day_replaces_rather_than_duplicates(self):
        appmod.store.clear(appmod.REPORTS)
        call("POST", "/api/collect", {})
        call("POST", "/api/collect", {})
        _, data = call("GET", "/api/state")
        self.assertEqual(len(data["history"]), 1,
                         "two reports dated the same Tuesday is unreadable history")

    def test_a_silly_window_is_refused_in_plain_english(self):
        status, data = call("POST", "/api/collect", {"windowDays": 400})
        self.assertEqual(status, 400)
        self.assertIn("between 1 and 14", data["error"])


class TestReports(unittest.TestCase):
    def setUp(self):
        appmod.store.clear(appmod.REPORTS)
        appmod.store.save(appmod.REPORTS, fake_report("2026-07-30"))
        appmod.store.save(appmod.REPORTS, fake_report("2026-07-31"))

    def test_fetching_one_by_date(self):
        status, data = call("GET", "/api/report/2026-07-30")
        self.assertEqual(status, 200)
        self.assertEqual(data["id"], "2026-07-30")

    def test_a_date_with_no_report_is_a_404_not_an_empty_one(self):
        status, data = call("GET", "/api/report/1999-01-01")
        self.assertEqual(status, 404)
        self.assertIn("No report", data["error"])

    def test_markdown_rendering_is_served(self):
        status, data = call("GET", "/api/markdown/2026-07-31")
        self.assertEqual(status, 200)
        self.assertIn("# AI radar", data["markdown"])


class TestKept(unittest.TestCase):
    def setUp(self):
        appmod.store.clear(appmod.KEPT)

    def test_keep_then_list(self):
        status, data = call("POST", "/api/kept", {
            "title": "Pricing dropped", "url": "https://example.com/1",
            "source": "OpenAI"})
        self.assertEqual(status, 200)
        kept_id = data["kept"]["id"]

        status, data = call("GET", "/api/kept")
        self.assertEqual([k["id"] for k in data["kept"]], [kept_id])

    def test_a_note_can_be_added_afterwards(self):
        _, data = call("POST", "/api/kept", {"title": "T", "url": "https://e.com/1"})
        kept_id = data["kept"]["id"]
        status, data = call("PATCH", "/api/kept/" + kept_id,
                            {"note": "Means their quoting tool gets cheaper"})
        self.assertEqual(status, 200)
        self.assertIn("quoting", data["kept"]["note"])

    def test_keeping_without_a_title_is_refused(self):
        status, data = call("POST", "/api/kept", {"url": "https://example.com/1"})
        self.assertEqual(status, 400)
        self.assertIn("title", data["error"])

    def test_removing_something_that_is_not_there(self):
        status, _ = call("DELETE", "/api/kept/nope")
        self.assertEqual(status, 404)

    def test_kept_survives_a_new_collection(self):
        """The kept list is his, not the collector's. A new report must not
        clear what he flagged yesterday."""
        call("POST", "/api/kept", {"title": "Keep me", "url": "https://e.com/9"})
        call("POST", "/api/collect", {})
        _, data = call("GET", "/api/kept")
        self.assertEqual(len(data["kept"]), 1)


class TestStaleness(unittest.TestCase):
    def test_a_fresh_report_is_not_flagged(self):
        import time
        report = fake_report()
        report["generated"] = int(time.time() * 1000)
        self.assertFalse(appmod._staleness(report)["stale"])

    def test_an_old_report_says_how_old_in_plain_english(self):
        import time
        report = fake_report()
        report["generated"] = int(time.time() * 1000) - (3 * 86_400_000)
        out = appmod._staleness(report)
        self.assertTrue(out["stale"])
        self.assertIn("3 days", out["line"])


if __name__ == "__main__":
    unittest.main()
