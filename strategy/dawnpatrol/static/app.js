/* app.js — Dawnpatrol.

   One GET /api/state carries everything the screen needs. The ranking is never
   recomputed here: it is computed in Python, and the score plus the sentence
   explaining it travel together. A rule implemented twice is a rule with two
   versions, and the two drift.

   Plain script, no modules, no build step, like every other app here. */

(function () {
  "use strict";

  var el = UI.el, $ = UI.$, api = UI.api, toast = UI.toast, guard = UI.guard;

  var state = null;
  var shown = null;        // the report currently on screen
  var collecting = false;

  // ------------------------------------------------------------- helpers

  function bandLabel(key) {
    var b = state && state.bands && state.bands[key];
    return (b && b.title) || key || "other";
  }

  function when(ms) {
    if (!ms) return "undated";
    return UI.fmtAgo(ms);
  }

  function keptKey(story) { return (story.url || story.title || "").toLowerCase(); }

  function isKept(story) {
    if (!state || !state.kept) return null;
    var k = keptKey(story);
    for (var i = 0; i < state.kept.length; i++) {
      if ((state.kept[i].url || state.kept[i].title || "").toLowerCase() === k) {
        return state.kept[i];
      }
    }
    return null;
  }

  // ------------------------------------------------------------- render

  function storyCard(s, index) {
    var card = el("div", { class: "story" + (index < 3 ? " is-top" : "") }, []);

    card.appendChild(el("div", { class: "story-head" }, [
      el("div", { class: "story-rank", text: String(index + 1) }, []),
      el("div", { class: "story-title" }, [
        el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer",
                  text: s.title }, [])
      ])
    ]));

    var meta = el("div", { class: "story-meta" }, [
      el("span", { class: "band is-" + s.band, text: bandLabel(s.band) }, []),
      el("span", { text: s.source }, []),
      el("span", { text: "·" }, []),
      el("span", { text: when(s.published) }, [])
    ]);
    if (s.sourcesCount > 1) {
      meta.appendChild(el("span", { class: "badge accent",
                                    text: s.sourcesCount + " sources" }, []));
    }
    card.appendChild(meta);

    if (s.summary) {
      card.appendChild(el("div", { class: "story-summary", text: s.summary }, []));
    }

    if (s.also && s.also.length) {
      var also = el("div", { class: "story-also" }, [
        el("span", { class: "muted", text: "Also: " }, [])
      ]);
      s.also.forEach(function (a, i) {
        if (i) also.appendChild(document.createTextNode(", "));
        also.appendChild(el("a", { href: a.url, target: "_blank",
                                   rel: "noopener noreferrer", text: a.source }, []));
      });
      card.appendChild(also);
    }

    /* The arithmetic, visible. 05 §8 forbids a figure without its working, and
       a ranking is a figure. */
    card.appendChild(el("div", { class: "story-why",
      text: "ranked " + s.score + " — " + (s.why || []).join("; ") }, []));

    var existing = isKept(s);
    var keepBtn = el("button", {
      class: "btn sm" + (existing ? " active" : ""),
      text: existing ? "Kept" : "Worth mentioning"
    }, []);
    /* Keeps immediately rather than opening a note box first. UI.prompt refuses
       an empty value, so an "optional note" dialog silently does nothing when
       you press OK without typing — and the note is genuinely optional. It is
       added from the kept list instead, where it is one click away. */
    keepBtn.addEventListener("click", function () {
      if (existing) {
        guard(api("DELETE", "/api/kept/" + encodeURIComponent(existing.id)), "Remove")
          .then(refresh);
      } else {
        guard(api("POST", "/api/kept", {
          title: s.title, url: s.url, source: s.source, note: ""
        }), "Keep").then(refresh);
      }
    });

    card.appendChild(el("div", { class: "story-actions" }, [keepBtn]));
    return card;
  }

  function renderStories() {
    var host = $("#stories");
    UI.clear(host);
    var stories = (shown && shown.stories) || [];
    $("#storiesEmpty").classList.toggle("hidden", stories.length > 0);
    stories.forEach(function (s, i) { host.appendChild(storyCard(s, i)); });

    var meta = $("#reportMeta");
    if (!shown) { meta.textContent = ""; return; }
    var c = shown.counts || {};
    meta.textContent = shown.id + " · " + c.sourcesOk + "/" + c.sourcesTried +
      " sources answered · " + c.itemsSeen + " items seen · " +
      c.storiesAfterMerge + " distinct";
  }

  function renderBriefing() {
    var sec = $("#briefingSection");
    var text = shown && shown.summary;
    sec.hidden = !text;
    if (text) $("#briefing").textContent = text;

    /* When there is no briefing, say why on the footer rather than leaving a
       silently missing section. */
    var note = shown && shown.summaryError;
    $("#footNote").textContent = note
      ? "Briefing skipped — " + note
      : "Local only. The screen never needs the network — only the collector does";
  }

  function renderStaleness() {
    var box = $("#staleness");
    var s = state && state.staleness;
    var on = !!(s && s.stale && s.line);
    box.classList.toggle("hidden", !on);
    if (on) box.textContent = s.line;
  }

  function renderKept() {
    var kept = (state && state.kept) || [];
    $("#keptSection").hidden = kept.length === 0;
    $("#keptCount").textContent = kept.length +
      " " + UI.pluralize(kept.length, "story", "stories");
    var host = $("#kept");
    UI.clear(host);
    kept.forEach(function (k) {
      var grow = el("div", { class: "grow" }, [
        el("div", { class: "rtitle" }, [
          k.url ? el("a", { href: k.url, target: "_blank",
                            rel: "noopener noreferrer", text: k.title }, [])
                : el("span", { text: k.title }, [])
        ]),
        el("div", { class: "rsub", text: (k.source || "") + " · kept " + k.keptOn }, [])
      ]);
      if (k.note) grow.appendChild(el("div", { class: "rnote", text: k.note }, []));

      var noteBtn = el("button", {
        class: "btn sm", text: k.note ? "Edit note" : "Add note"
      }, []);
      noteBtn.addEventListener("click", function () {
        UI.prompt({
          title: k.title,
          label: "What does this mean for a 20-person company?",
          placeholder: "One sentence you would actually say out loud",
          hint: "Plain English. This is what you say on the call, not a summary.",
          value: k.note || ""
        }, function (note) {
          guard(api("PATCH", "/api/kept/" + encodeURIComponent(k.id), { note: note }),
                "Save note").then(refresh);
        });
      });

      var drop = el("button", { class: "btn sm ghost", text: "Remove" }, []);
      drop.addEventListener("click", function () {
        guard(api("DELETE", "/api/kept/" + encodeURIComponent(k.id)), "Remove")
          .then(refresh);
      });
      host.appendChild(el("div", { class: "row" }, [grow, noteBtn, drop]));
    });
  }

  function renderHealth() {
    var failed = (shown && shown.failed) || [];
    var quiet = (shown && shown.quiet) || [];
    var dead = (state && state.deadFeeds) || [];
    $("#healthSection").hidden = !(failed.length || quiet.length);

    var host = $("#health");
    UI.clear(host);
    failed.forEach(function (f) {
      host.appendChild(el("div", { class: "row" }, [
        el("div", { class: "grow" }, [
          el("div", { class: "rtitle", text: f.name }, []),
          el("div", { class: "rsub", text: f.error }, [])
        ]),
        el("span", { class: "badge bad", text: "failed" }, [])
      ]));
    });
    quiet.forEach(function (q) {
      host.appendChild(el("div", { class: "row" }, [
        el("div", { class: "grow" }, [
          el("div", { class: "rtitle", text: q.name }, []),
          el("div", { class: "rsub", text: "reachable, nothing in it right now" }, [])
        ]),
        el("span", { class: "badge", text: "quiet" }, [])
      ]));
    });
    if (dead.length) {
      host.appendChild(el("div", { class: "dead",
        text: dead.length + " more were tried once and have no working feed: " +
              dead.map(function (d) { return d.name; }).join(", ") +
              ". Recorded in sources.py so nobody tries them twice." }, []));
    }
  }

  function renderHistory() {
    var hist = (state && state.history) || [];
    $("#historySection").hidden = hist.length < 2;
    var host = $("#history");
    UI.clear(host);
    hist.forEach(function (h) {
      var btn = el("button", {
        class: "hbtn" + (shown && h.id === shown.id ? " active" : ""),
        title: h.stories + " stories, " + h.sourcesOk + " sources answered"
      }, [h.id]);
      if (h.sourcesFailed > 0) {
        btn.appendChild(el("span", { class: "warn-dot", text: " •" }, []));
      }
      btn.addEventListener("click", function () { open(h.id); });
      host.appendChild(btn);
    });
  }

  function renderAll() {
    renderStaleness();
    renderBriefing();
    renderStories();
    renderKept();
    renderHealth();
    renderHistory();
  }

  // ------------------------------------------------------------- data

  function refresh() {
    return api("GET", "/api/state").then(function (data) {
      state = data;
      if (!shown || (data.report && data.report.id === (shown && shown.id))) {
        shown = data.report;
      }
      renderAll();
      return data;
    });
  }

  function open(id) {
    if (shown && shown.id === id) return;
    guard(api("GET", "/api/report/" + encodeURIComponent(id)), "Open")
      .then(function (doc) { shown = doc; renderAll(); });
  }

  function collect() {
    if (collecting) return;
    collecting = true;
    var btn = $("#btnCollect");
    btn.textContent = "Collecting…";
    document.body.classList.add("collecting");

    api("POST", "/api/collect", {})
      .then(function (data) {
        shown = data.report;
        return refresh();
      })
      .then(function () {
        var c = (shown && shown.counts) || {};
        if (c.sourcesFailed) {
          toast.warn(c.sourcesOk + " of " + c.sourcesTried +
                     " sources answered — the rest are listed below");
        } else {
          toast.good(c.storiesAfterMerge + " stories from " + c.sourcesOk + " sources");
        }
      })
      .catch(function (err) {
        toast.bad("Collect failed: " + (err && err.message ? err.message : err));
      })
      .then(function () {
        collecting = false;
        btn.textContent = "Collect now";
        document.body.classList.remove("collecting");
      });
  }

  function copyMarkdown() {
    if (!shown) { toast.warn("Nothing to copy yet"); return; }
    guard(api("GET", "/api/markdown/" + encodeURIComponent(shown.id)), "Copy")
      .then(function (d) { UI.copy(d.markdown); });
  }

  // ------------------------------------------------------------- boot

  $("#themeSlot").appendChild(UI.themeButton());
  $("#btnCollect").addEventListener("click", collect);
  $("#btnCopy").addEventListener("click", copyMarkdown);
  UI.hotkey("ctrl+r", collect, true);

  refresh()
    .then(function () {
      if (state && !state.claudeAvailable) {
        $("#subtitle").textContent = "What happened, ranked for your clients";
      }
      UI.booted();
    })
    .catch(function (err) {
      toast.bad("Could not load: " + (err && err.message ? err.message : err));
      UI.booted();
    });
})();
