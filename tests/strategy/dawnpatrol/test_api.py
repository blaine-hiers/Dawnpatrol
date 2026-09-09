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
