"""synth — the optional written summary on top of the structured digest.

If the `claude` CLI happens to be on this machine, the headless run can add a
few paragraphs of "so what" to the ranked list. If it is not there, is not
logged in, or takes too long, **the report still ships**, unchanged, minus one
section.

That ordering is the whole design. Every other app here runs on the standard
library and nothing else, and this one is not allowed to be the exception that
makes a morning report depend on a subscription, a network, and a login. The
digest is the product; this is a garnish that is permitted to fail.

The prompt is written to the same rules the documents are: plain English, no
consultant register, ranges rather than single-point figures, and an explicit
instruction not to invent a number that was not in the input.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

__all__ = ["available", "claude_path", "summarise", "audience", "prompt",
           "AUDIENCE_ENV", "DEFAULT_AUDIENCE", "PROMPT"]

TIMEOUT_SECONDS = 180

# Who the briefing is for. This is the one part of the prompt that is anyone's
# business but the tool's, so it is a setting rather than a literal: a ranking
# tuned to somebody else's customers is worth nothing to you. Set
# DAWNPATROL_AUDIENCE to a sentence or two describing yours.
AUDIENCE_ENV = "DAWNPATROL_AUDIENCE"
DEFAULT_AUDIENCE = (
    "owner-led small and medium businesses: 10-50 employees, $2M-$20M "
    "revenue, trades and light manufacturing and distribution. Owners, not "
    "IT managers. Most have no internal IT at all."
)


def audience() -> str:
    """The reader's own customers, read from the environment, falling back to a
    generic small-business profile. Whitespace-only counts as unset --- an empty
    audience would quietly produce a briefing ranked for nobody."""
    return os.environ.get(AUDIENCE_ENV, "").strip() or DEFAULT_AUDIENCE


PROMPT_TEMPLATE = """\
You are briefing a solo technology and AI advisor. Their clients are \
{audience}

Below is today's collected AI news as JSON. Write them a short briefing.

Rules, all of them hard:
- Plain English. Short sentences. No word they would not say out loud in an \
owner's office. No "leverage", "solutions", "landscape", "transformative".
- Lead with what changed that would alter advice they give a client this \
month. If nothing did, say so plainly in the first line. A quiet day is a real \
finding and pretending otherwise wastes their time.
- Never invent a number, a statistic or a dollar figure. Use only what is in \
the input. If you give a figure, name the source in the same sentence.
- Never state a single-point ROI or a single-point saving. Ranges only, and \
show the arithmetic.
- At most 400 words.

Structure it as:
**What actually changed** - two or three sentences, or one line saying nothing did.
**Worth mentioning on a call** - up to three bullets. For each, name the story \
and say in one sentence what it means for a 20-person company. Skip this \
section entirely rather than padding it.
**Ignore for now** - one line on what is loud but irrelevant to their clients.

Today's collected news:
"""


def prompt() -> str:
    """The full prompt, with the configured audience folded in."""
    return PROMPT_TEMPLATE.format(audience=audience())


# Kept for callers that read the constant. It is the prompt as configured at
# import time rather than a fixed string --- prefer prompt() where the
# environment may have changed since.
PROMPT = prompt()


def claude_path() -> str:
    """Where the CLI is, or "". Checked rather than assumed --- it is installed
    per-user on Windows and is frequently absent from a service account's PATH,
    which is exactly the account a scheduled task runs under."""
    found = shutil.which("claude")
    if found:
        return found
    candidates = [
        os.path.expandvars(r"%USERPROFILE%\.local\bin\claude.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\claude\claude.exe"),
        os.path.expanduser("~/.local/bin/claude"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return ""


def available() -> bool:
    return bool(claude_path())


def _payload(report: dict, top: int = 20) -> str:
    """Only what is needed to write the briefing. The scoring arithmetic and
    the source-health block are for the reader, not for this."""
    slim = []
    for s in report.get("stories", [])[:top]:
        slim.append({
            "title": s.get("title", ""),
            "source": s.get("source", ""),
            "alsoCarriedBy": [a.get("source") for a in s.get("also", [])],
            "summary": (s.get("summary") or "")[:400],
            "url": s.get("url", ""),
        })
    return json.dumps({"stories": slim}, ensure_ascii=False, indent=1)


def summarise(report: dict, timeout: int = TIMEOUT_SECONDS) -> tuple[str, str]:
    """(text, error). Never raises --- a failure here must not lose the report."""
    exe = claude_path()
    if not exe:
        return "", ("the claude CLI is not on this machine, so the report is the "
                    "ranked list only")
    if not report.get("stories"):
        return "", "nothing collected, so there was nothing to summarise"

    # prompt(), not PROMPT --- a scheduled run may set DAWNPATROL_AUDIENCE after
    # this module was imported, and a stale audience is a briefing for the
    # wrong reader.
    full_prompt = prompt() + _payload(report)
    try:
        proc = subprocess.run(
            [exe, "-p", full_prompt],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL,      # headless: never wait on a prompt
        )
    except subprocess.TimeoutExpired:
        return "", f"claude did not answer within {timeout}s — ranked list only"
    except OSError as e:
        return "", f"could not run claude ({e}) — ranked list only"

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        tail = detail[-1][:160] if detail else f"exit code {proc.returncode}"
        return "", f"claude failed: {tail} — ranked list only"

    text = (proc.stdout or "").strip()
    if not text:
        return "", "claude returned nothing — ranked list only"
    return text, ""


if __name__ == "__main__":                      # a way to check it by hand
    print("claude:", claude_path() or "not found")
    sys.exit(0 if available() else 1)
