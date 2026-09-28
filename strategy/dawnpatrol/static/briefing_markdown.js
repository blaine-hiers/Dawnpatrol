/* briefing_markdown.js — the one Markdown feature the briefing needs: **bold**.

   synth.py's prompt asks the model for prose with `**bold**` section headers
   (see PROMPT_TEMPLATE), and that text used to be dropped onto the screen
   with `textContent`, so the card showed literal asterisks instead of bold
   (issue #11).

   This repo has a standing rule against Markdown libraries (feeds.py's
   docstring: "no feedparser here for the same reason there is no Markdown
   library in `hub/`"), and setting `innerHTML` from model-generated text
   would be an injection risk regardless of that rule. So this parses only
   `**bold**` -- nothing else -- into plain data (never HTML): a list of
   {bold, text} segments. app.js turns that into real DOM nodes (`<strong>`
   or a text node) and never touches `innerHTML`, so nothing the model
   writes, including a literal `<script>` tag, can ever be interpreted as
   markup; it can only ever be text.

   Kept as its own tiny, dependency-free file (no `UI`, no `document`) rather
   than folded into app.js, specifically so it can be required and tested
   under plain Node with no browser and no stubbing. */
(function (root) {
  "use strict";

  var BOLD_RX = /\*\*([^*]+)\*\*/g;

  /** parseBriefingSegments(text) -> [{bold: boolean, text: string}, ...]
   *  Concatenating every segment's `text` in order reproduces `text` with
   *  the `**` markers stripped and nothing else changed. */
  function parseBriefingSegments(text) {
    text = String(text || "");
    var segments = [];
    var last = 0;
    var m;
    BOLD_RX.lastIndex = 0;
    while ((m = BOLD_RX.exec(text)) !== null) {
      if (m.index > last) segments.push({ bold: false, text: text.slice(last, m.index) });
      segments.push({ bold: true, text: m[1] });
      last = m.index + m[0].length;
    }
    if (last < text.length) segments.push({ bold: false, text: text.slice(last) });
    return segments;
  }

  var api = { parseBriefingSegments: parseBriefingSegments };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;          // `node -e "require('./briefing_markdown.js')"`
  } else {
    root.BriefingMD = api;         // browser: window.BriefingMD
  }
})(typeof window !== "undefined" ? window : this);
