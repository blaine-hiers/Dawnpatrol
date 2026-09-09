# Dawnpatrol

What happened in AI yesterday, swept from 27 feeds before the day starts and
ranked for the kind of company you actually advise — not for AI in general.

```
py strategy/dawnpatrol/app.py     # opens in your browser
py strategy/dawnpatrol/app.py --once    # headless, for a 6am scheduled run
py strategy/dawnpatrol/app.py --check   # are the feeds still alive?
py run_all_tests.py               # 67 tests
```

Windows: double-click `run.cmd`.

## What it does that a feed reader does not

**It collapses the story, not the articles.** One announcement carried by six
outlets becomes one story with six links, so a big day does not look like six
big days.

**It ranks for a stated audience.** Set `DAWNPATROL_AUDIENCE` to a sentence or
two describing your customers and the written briefing is tuned to them. The
default is a generic small-business profile. A ranking tuned to somebody else's
customers is worth nothing to you.

**Nothing is ever filtered out.** Everything collected is kept and ordered. A
tool that quietly hides what it judged uninteresting narrows what you know
without telling you — and you cannot audit an absence.

**A broken source is on the screen, not in a log.** A feed that 404s looks
exactly like a quiet week, and arXiv being empty on a Saturday is a third thing
again. All three are reported differently, because treating them the same is how
you end up confidently briefing on a source that died in March.

**The ranking shows its arithmetic.** Every score can be taken apart.

## The feed list was checked, not assembled

Every feed in `sources.py` was fetched before it was written down. The eight
that 404'd are **recorded in the file with their failure** rather than deleted,
so nobody researches them twice — including one major lab that publishes no
public feed at all.

## The optional briefing

If the `claude` CLI happens to be on the machine, a headless run adds a few
paragraphs of "so what" on top of the ranked list. If it is not there, is not
logged in, or takes too long, **the report still ships**, unchanged, minus one
section.

That ordering is the whole design. Everything else here runs on the standard
library and nothing else, and this one part is not allowed to become the
exception that makes a morning report depend on a subscription, a network and a
login. The digest is the product; the briefing is a garnish that is permitted to
fail.

The user agent carries no email address and no private domain. It goes to
twenty-seven servers a day.

## Scheduling it

`strategy/dawnpatrol/install-schedule.cmd` registers a Windows scheduled task
for a 6am collection. The task runs `--once`, which writes a report and exits.

## Layout

```
_shared/               server, storage and design system (vendored — see below)
strategy/dawnpatrol/   the app
tests/                 67 tests, mirroring the app tree
```

`_shared/` is vendored from a larger private workspace of about twenty of these
apps that share one server base, one storage layer and one visual language. The
two-level `strategy/…` path is not decoration: the folder an app sits in decides
its colour, and the test tree mirrors the app tree exactly. Its comments
occasionally mention sibling apps that are not in this repo.

`tests/test_publishable.py` guards the seam between that private workspace and
this one — it fails if a copied file brings private fixture data back with it.

## Licence

MIT. See [`LICENSE`](LICENSE).
