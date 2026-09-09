"""Dawnpatrol — what happened in AI yesterday, ranked for a small-business advisor.

Run it:  py app.py              the screen
         py app.py --once       collect, write, exit. No server, no browser
         py app.py --check      probe every source and say which ones answer

Why this exists
---------------
The positioning in `01` is **Tech & AI advisor**, and the age objection in
`04` Section 6 is answered with competence rather than with years. Both of those
cash out the same way: walking into a room knowing what happened last week.
There is no way to do that from memory, and reading twenty-five feeds by hand at
6am is not a plan, it is an intention.

So this reads them, collapses the duplicates, ranks what is left for *this*
business rather than for AI generally, and writes one report.

What it is careful about
------------------------
* **A source that breaks is reported, never skipped.** A feed that 404s looks
  exactly like a quiet week, and the difference matters every single morning.
* **Nothing is filtered.** Everything collected is kept and ordered. Ranking
  low is not the same as being hidden, and a tool that decides what he is
  allowed to see would be a bad tool to trust for two years.
* **The ranking shows its working**, per `05` Section 8.
* **Offline is a state, not a crash.** No network means yesterday's report, on
  screen, labelled stale.

Everything lives in `data/radar.db` next to this file, which means it also
reaches `../../ai-context/` through the hub's generic exporter for free.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(next(p / "_shared" for p in Path(__file__).resolve().parents
                            if (p / "_shared" / "appkit.py").is_file())))

from appkit import App, HttpError                    # noqa: E402
from store import Store                              # noqa: E402

import digest                                        # noqa: E402
import feeds                                         # noqa: E402
import sources                                       # noqa: E402
import synth                                         # noqa: E402

ROOT = Path(__file__).resolve().parent
app = App("Dawnpatrol", ROOT)
store = Store(ROOT / "data" / "radar.db")

REPORTS = "reports"
KEPT = "kept"

WINDOW_DAYS = 3
KEEP_REPORTS = 60          # two months of mornings is plenty of history


def _today_id(when_ms: int | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime((when_ms or int(time.time() * 1000)) / 1000))


# ---------------------------------------------------------------- collecting

def collect(window_days: int = WINDOW_DAYS, with_summary: bool = True) -> dict:
    """Fetch everything, build the report, store it under today's date.

    Re-running on the same day **replaces** that day's report rather than
    adding a second one. Two reports dated the same Tuesday is a history you
    cannot read.
    """
    results = feeds.fetch_all(sources.SOURCES)
    report = digest.build(results, window_days=window_days)

    report["id"] = _today_id(report["generated"])
    report["summary"] = ""
    report["summaryError"] = ""

    if with_summary:
        text, err = synth.summarise(report)
        report["summary"], report["summaryError"] = text, err

    existing = store.get(REPORTS, report["id"])
    if existing:
        report["created"] = existing.get("created", report["generated"])
    store.save(REPORTS, report)
    _prune()
    return report


def _prune() -> None:
    old = store.all(REPORTS)[KEEP_REPORTS:]
    for doc in old:
        store.delete(REPORTS, doc["id"])


def _latest() -> dict | None:
    got = store.all(REPORTS, limit=1)
    return got[0] if got else None


def _history() -> list[dict]:
    return [{"id": r["id"],
             "generated": r.get("generated", 0),
             "stories": len(r.get("stories", [])),
             "sourcesOk": r.get("counts", {}).get("sourcesOk", 0),
             "sourcesFailed": r.get("counts", {}).get("sourcesFailed", 0)}
            for r in store.all(REPORTS)]


def _staleness(report: dict | None) -> dict:
    if not report:
        return {"stale": True, "hours": 0,
                "line": "Nothing collected yet. Press Collect now."}
    hours = (int(time.time() * 1000) - report.get("generated", 0)) / 3_600_000
    if hours < 18:
        return {"stale": False, "hours": round(hours, 1), "line": ""}
    days = hours / 24
    when = f"{int(hours)} hours" if hours < 48 else f"{days:.0f} days"
    return {"stale": True, "hours": round(hours, 1),
            "line": f"This report is {when} old. Nothing has collected since."}


# ---------------------------------------------------------------- routes

@app.api("GET", "/api/state")
def state(req):
    report = _latest()
    return {
        "report": report,
        "history": _history(),
        "kept": store.all(KEPT),
        "staleness": _staleness(report),
        "bands": sources.BANDS,
        "sourceCount": len(sources.SOURCES),
        "deadFeeds": [{"name": n, "url": u, "why": w}
                      for n, u, w in sources.DEAD_FEEDS],
        "claudeAvailable": synth.available(),
    }


@app.api("POST", "/api/collect")
def do_collect(req):
    body = req.body if isinstance(req.body, dict) else {}
    window = int(body.get("windowDays") or WINDOW_DAYS)
    if not 1 <= window <= 14:
        raise HttpError(400, "The window has to be between 1 and 14 days.")
    report = collect(window_days=window,
                     with_summary=bool(body.get("summary", True)))
    return {"report": report, "history": _history(),
            "staleness": _staleness(report)}


@app.api("GET", "/api/report/<report_id>")
def one_report(req):
    doc = store.get(REPORTS, req.params["report_id"])
    if doc is None:
        raise HttpError(404, "No report for that date.")
    return doc


@app.api("GET", "/api/kept")
def list_kept(req):
    return {"kept": store.all(KEPT)}


@app.api("POST", "/api/kept")
def keep(req):
    body = req.json()
    title = str(body.get("title") or "").strip()
    url = str(body.get("url") or "").strip()
    if not title:
        raise HttpError(400, "Keep needs the story's title.")
    doc = store.save(KEPT, {
        "title": title,
        "url": url,
        "source": str(body.get("source") or "").strip(),
        "note": str(body.get("note") or "").strip(),
        "keptOn": _today_id(),
    })
    return {"kept": doc}


@app.api("PATCH", "/api/kept/<kept_id>")
def edit_kept(req):
    body = req.json()
    doc = store.patch(KEPT, req.params["kept_id"],
                      {"note": str(body.get("note") or "").strip()})
    if doc is None:
        raise HttpError(404, "That one is not in the kept list.")
    return {"kept": doc}


@app.api("DELETE", "/api/kept/<kept_id>")
def unkeep(req):
    if not store.delete(KEPT, req.params["kept_id"]):
        raise HttpError(404, "That one is not in the kept list.")
    return {"removed": True}


@app.api("GET", "/api/markdown/<report_id>")
def as_markdown(req):
    doc = store.get(REPORTS, req.params["report_id"])
    if doc is None:
        raise HttpError(404, "No report for that date.")
    return {"markdown": digest.render_markdown(doc)}


# ---------------------------------------------------------------- headless

def _run_once(argv: list[str]) -> int:
    """Collect and write. Exit code is the answer, so a scheduler can see it.

    0  a report was written
    1  nothing could be collected at all --- every source failed
    2  it ran, but more than half the sources failed
    """
    quiet = "--quiet" in argv
    no_summary = "--no-summary" in argv
    window = WINDOW_DAYS
    for i, a in enumerate(argv):
        if a == "--days" and i + 1 < len(argv):
            try:
                window = max(1, min(14, int(argv[i + 1])))
            except ValueError:
                pass

    started = time.time()
    report = collect(window_days=window, with_summary=not no_summary)
    counts = report.get("counts", {})
    ok, tried = counts.get("sourcesOk", 0), counts.get("sourcesTried", 0)

    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    md_path = out_dir / f"{report['id']}.md"
    body = digest.render_markdown(report)
    if report.get("summary"):
        body = (body.split("\n---\n")[0]
                + "\n## The briefing\n\n" + report["summary"] + "\n\n---\n"
                + body.split("\n---\n")[-1])
    md_path.write_text(body, encoding="utf-8")

    if not quiet:
        print(f"Dawnpatrol — {report['id']}")
        print(f"  {ok}/{tried} sources answered in {time.time() - started:.1f}s")
        print(f"  {counts.get('storiesAfterMerge', 0)} distinct stories, "
              f"{counts.get('shown', 0)} kept")
        if report.get("summaryError"):
            print(f"  briefing: {report['summaryError']}")
        elif report.get("summary"):
            print("  briefing: written")
        for f in report.get("failed", []):
            print(f"  ! {f['name']}: {f['error']}")
        print(f"  -> {md_path}")

    if ok == 0:
        return 1
    return 2 if ok < tried / 2 else 0


def _check(argv: list[str]) -> int:
    """Probe every source. Nothing is stored --- this only answers 'is it alive'."""
    results = feeds.fetch_all(sources.SOURCES)
    width = max(len(r.name) for r in results)
    bad = 0
    for r in results:
        if r.ok:
            print(f"  ok   {r.name:<{width}}  {len(r.items):>4} items  {r.note}")
        else:
            bad += 1
            print(f"  FAIL {r.name:<{width}}  {r.error}")
    print(f"\n{len(results) - bad}/{len(results)} answered.")
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--once" in args:
        raise SystemExit(_run_once(args))
    if "--check" in args:
        raise SystemExit(_check(args))
    app.run()
