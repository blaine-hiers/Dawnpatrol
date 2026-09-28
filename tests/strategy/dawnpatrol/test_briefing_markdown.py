"""briefing_markdown.js — the hand-rolled **bold** parser behind the
briefing card (issue #11).

The rest of this app's front end has no test runner (plain script, no
build step, no framework — see app.js's own header comment), so this file
is the one place that shells out to `node` rather than the interpreter
`run_all_tests.py` otherwise assumes. `briefing_markdown.js` was written
specifically to make that possible without a browser or a DOM stub: it
touches no `window`/`document` global, so `node -e "require(...)"` alone
is enough to exercise it directly, same as any other unit under test here.

If `node` is not on this machine, the suite skips rather than failing --
a missing optional dev tool is not the same fact as a broken parser, and
`py run_all_tests.py` must stay green either way on a dev machine that
simply doesn't have Node.

That leniency does not extend to CI. `CI` is a variable GitHub Actions
(and effectively every other CI system) always sets, and this repo's own
workflow does not install Node explicitly -- so a runner image that ever
loses its bundled Node would silently *skip* the one test that proves a
`<script>` in model output can't execute, rather than failing the build.
Skipping is only for "this optional tool isn't installed here"; in CI,
where Node is assumed to be present, its absence is a real failure.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

_APP = Path(__file__).resolve().parents[3] / "strategy" / "dawnpatrol"
_SCRIPT = _APP / "static" / "briefing_markdown.js"

NODE = shutil.which("node")
IN_CI = bool(os.environ.get("CI"))


def _parse(text: str) -> list[dict]:
    """Runs the real browser file under Node and returns what it parsed."""
    js = (
        "const mod = require(process.argv[1]);"
        "process.stdout.write(JSON.stringify(mod.parseBriefingSegments(process.argv[2])));"
    )
    proc = subprocess.run(
        [NODE, "-e", js, "--", str(_SCRIPT), text],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


class TestParseBriefingSegments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if NODE:
            return
        if IN_CI:
            # Fail, not skip: CI must not silently drop the injection test
            # just because the runner happened to lack Node (issue #11).
            raise AssertionError(
                "node is not installed, and the CI env var is set -- "
                "failing instead of skipping so this can't go unnoticed."
            )
        raise unittest.SkipTest("node is not installed on this machine")

    def test_plain_text_is_one_unbolded_segment(self):
        segs = _parse("nothing changed this week")
        self.assertEqual(segs, [{"bold": False, "text": "nothing changed this week"}])

    def test_a_bold_span_becomes_its_own_segment(self):
        segs = _parse("**What actually changed** - nothing did.")
        self.assertEqual(segs, [
            {"bold": True, "text": "What actually changed"},
            {"bold": False, "text": " - nothing did."},
        ])

    def test_every_asterisk_marker_is_gone_from_the_output(self):
        segs = _parse("**Worth mentioning** - one bullet. **Ignore for now** - none.")
        joined = "".join(s["text"] for s in segs)
        self.assertNotIn("*", joined)
        self.assertEqual(
            joined,
            "Worth mentioning - one bullet. Ignore for now - none.",
        )

    def test_script_tag_in_model_output_survives_as_literal_text(self):
        """The parser's job is only to find `**bold**`; it must never treat
        `<script>` (or any other markup the model might emit) as anything
        but characters. app.js is what actually makes this inert -- it
        turns every segment into `el(...)`/`createTextNode`, never
        `innerHTML` -- but that guarantee is worthless if the parser itself
        already mangled or swallowed the tag."""
        payload = "**Alert** - <script>alert(1)</script> was mentioned today."
        segs = _parse(payload)
        self.assertEqual(segs, [
            {"bold": True, "text": "Alert"},
            {"bold": False, "text": " - <script>alert(1)</script> was mentioned today."},
        ])

    def test_an_unclosed_bold_marker_is_left_as_plain_text(self):
        """Malformed model output (odd number of `**`) must not swallow the
        rest of the briefing or throw -- it is just text with no match."""
        segs = _parse("**unfinished bold with no closing marker")
        self.assertEqual(segs, [
            {"bold": False, "text": "**unfinished bold with no closing marker"},
        ])

    def test_empty_text_is_no_segments(self):
        self.assertEqual(_parse(""), [])


if __name__ == "__main__":
    unittest.main()
