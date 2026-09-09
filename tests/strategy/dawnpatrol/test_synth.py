"""synth — the one module that leaves the standard library, and the one
failure path the README makes a promise about: if `claude` is missing, not
logged in, or too slow, the report still ships, unchanged, minus one section.

Nothing here touches the network or a real `claude` binary. `shutil.which`,
`os.path.isfile` and `subprocess.run` are stubbed in every test, so this suite
passes identically whether or not the CLI happens to be installed on the
machine running it.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
_ROOT = next(p for p in _TESTS.parents if (p / "_shared" / "appkit.py").is_file())
_APP = _ROOT / _TESTS.relative_to(_ROOT / "tests")
sys.path.insert(0, str(_APP))
sys.path.insert(0, str(_ROOT / "_shared"))

import digest  # noqa: E402
import feeds  # noqa: E402
import synth  # noqa: E402


class TestClaudePath(unittest.TestCase):
    def test_absent_from_path_and_every_known_location_is_empty(self):
        with mock.patch.object(synth.shutil, "which", return_value=None), \
             mock.patch.object(synth.os.path, "isfile", return_value=False):
            self.assertEqual(synth.claude_path(), "")
            self.assertFalse(synth.available())

    def test_found_on_path_is_used_and_reported_available(self):
        with mock.patch.object(synth.shutil, "which",
                               return_value=r"C:\fake\claude.exe"):
            self.assertEqual(synth.claude_path(), r"C:\fake\claude.exe")
            self.assertTrue(synth.available())


class TestSummariseMissingBinary(unittest.TestCase):
    def test_returns_cleanly_with_the_ranked_list_only_message(self):
        report = {"stories": [{"title": "A story", "source": "S", "url": "u"}]}
        with mock.patch.object(synth, "claude_path", return_value=""):
            text, err = synth.summarise(report)
        self.assertEqual(text, "")
        self.assertIn("not on this machine", err)

    def test_nothing_collected_is_its_own_message_and_never_raises(self):
        with mock.patch.object(synth, "claude_path",
                               return_value=r"C:\fake\claude.exe"):
            text, err = synth.summarise({"stories": []})
        self.assertEqual(text, "")
        self.assertIn("nothing to summarise", err)


class TestSummariseFailureModes(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(synth, "claude_path",
                                    return_value=r"C:\fake\claude.exe")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.report = {"stories": [{"title": "A story", "source": "S", "url": "u"}]}

    def test_a_non_zero_exit_reports_the_last_line_of_stderr(self):
        proc = mock.Mock(returncode=1, stdout="", stderr="warming up\nsomething broke\n")
        with mock.patch.object(synth.subprocess, "run", return_value=proc):
            text, err = synth.summarise(self.report)
        self.assertEqual(text, "")
        self.assertIn("something broke", err)

    def test_a_timeout_is_reported_rather_than_raised(self):
        timeout_err = synth.subprocess.TimeoutExpired(cmd=["claude"], timeout=180)
        with mock.patch.object(synth.subprocess, "run", side_effect=timeout_err):
            text, err = synth.summarise(self.report, timeout=180)
        self.assertEqual(text, "")
        self.assertIn("180", err)

    def test_an_os_error_starting_the_process_is_reported_rather_than_raised(self):
        with mock.patch.object(synth.subprocess, "run",
                               side_effect=OSError("no such file")):
            text, err = synth.summarise(self.report)
        self.assertEqual(text, "")
        self.assertIn("could not run claude", err)

    def test_a_clean_exit_with_empty_stdout_is_reported_too(self):
        proc = mock.Mock(returncode=0, stdout="   ", stderr="")
        with mock.patch.object(synth.subprocess, "run", return_value=proc):
            text, err = synth.summarise(self.report)
        self.assertEqual(text, "")
        self.assertIn("nothing", err)


class TestSummariseSuccess(unittest.TestCase):
    def test_a_clean_run_returns_the_stripped_stdout_and_no_error(self):
        report = {"stories": [{"title": "A story", "source": "S", "url": "u"}]}
        proc = mock.Mock(returncode=0, stdout="  Here is the briefing.  \n", stderr="")
        with mock.patch.object(synth, "claude_path",
                               return_value=r"C:\fake\claude.exe"), \
             mock.patch.object(synth.subprocess, "run", return_value=proc):
            text, err = synth.summarise(report)
        self.assertEqual(text, "Here is the briefing.")
        self.assertEqual(err, "")


class TestAudience(unittest.TestCase):
    def setUp(self):
        self._had = synth.AUDIENCE_ENV in os.environ
        self._old = os.environ.get(synth.AUDIENCE_ENV)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._had:
            os.environ[synth.AUDIENCE_ENV] = self._old
        else:
            os.environ.pop(synth.AUDIENCE_ENV, None)

    def test_unset_falls_back_to_the_default(self):
        os.environ.pop(synth.AUDIENCE_ENV, None)
        self.assertEqual(synth.audience(), synth.DEFAULT_AUDIENCE)

    def test_set_is_used_verbatim(self):
        os.environ[synth.AUDIENCE_ENV] = "roofers and plumbers, 5-10 people"
        self.assertEqual(synth.audience(), "roofers and plumbers, 5-10 people")

    def test_whitespace_only_counts_as_unset(self):
        """The docstring is explicit about this: an empty audience would
        quietly produce a briefing ranked for nobody."""
        os.environ[synth.AUDIENCE_ENV] = "   \t  \n"
        self.assertEqual(synth.audience(), synth.DEFAULT_AUDIENCE)


class TestPrompt(unittest.TestCase):
    def setUp(self):
        self._had = synth.AUDIENCE_ENV in os.environ
        self._old = os.environ.get(synth.AUDIENCE_ENV)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._had:
            os.environ[synth.AUDIENCE_ENV] = self._old
        else:
            os.environ.pop(synth.AUDIENCE_ENV, None)

    def test_prompt_includes_the_configured_audience(self):
        os.environ[synth.AUDIENCE_ENV] = "left-handed locksmiths in Ohio"
        self.assertIn("left-handed locksmiths in Ohio", synth.prompt())

    def test_the_full_prompt_includes_the_story_titles_it_was_given(self):
        report = {"stories": [
            {"title": "Pricing drops for widget makers", "source": "S", "url": "u"},
            {"title": "A completely different headline", "source": "T", "url": "v"},
        ]}
        full_prompt = synth.prompt() + synth._payload(report)
        self.assertIn("Pricing drops for widget makers", full_prompt)
        self.assertIn("A completely different headline", full_prompt)


class TestEndToEndGuarantee(unittest.TestCase):
    """The README's promise, exercised directly: 'the report still ships,
    unchanged, minus one section.' Build the same digest twice from the same
    fixed input, run a failing synth over one copy, and check the digest
    itself never moved.
    """

    NOW = 1_785_000_000_000

    def _built(self):
        items = [feeds.Item("Pricing changes for small shops",
                            "https://example.com/a", "Src", "press",
                            self.NOW, "summary", True)]
        return digest.build(
            [feeds.FetchResult("Src", "u", "press", True, items=items)],
            now_ms=self.NOW)

    def test_a_failing_synth_still_yields_a_complete_report_with_the_same_digest(self):
        no_summary = self._built()
        no_summary["summary"], no_summary["summaryError"] = "", ""

        with_failing_synth = self._built()
        with mock.patch.object(synth, "claude_path", return_value=""):
            text, err = synth.summarise(with_failing_synth)
        with_failing_synth["summary"], with_failing_synth["summaryError"] = text, err

        self.assertEqual(text, "")
        self.assertTrue(err, "a failure must say why, not just come back empty")

        def digest_only(report):
            return {k: v for k, v in report.items()
                    if k not in ("summary", "summaryError")}

        self.assertEqual(digest_only(no_summary), digest_only(with_failing_synth))
        self.assertTrue(with_failing_synth["stories"],
                        "the report must still be complete, not just non-crashing")


if __name__ == "__main__":
    unittest.main()
