"""sources — where the radar reads from, and how much each one is trusted.

**Every feed in this file was fetched and parsed before it was written down**
(August 1, 2026). Eight candidates were dropped because they 404'd, and they
are recorded at the bottom rather than deleted --- a source list with the
failures pruned out invites the same eight to be tried again next year.

Bands, not a flat list. A story from a lab's own blog and the same story
rewritten by a news site are worth different amounts, and a report that cannot
tell them apart is a feed reader rather than a radar.
"""

from __future__ import annotations

__all__ = ["SOURCES", "BANDS", "KEYWORDS", "NOISE", "DEAD_FEEDS", "by_band"]

# weight multiplies an item's score; blurb is what the screen says about a band.
BANDS = {
    "lab": {
        "title": "From the labs",
        "weight": 1.35,
        "blurb": "The people who built it, saying what they built. No middleman",
    },
    "record": {
        "title": "The record",
        "weight": 1.10,
        "blurb": "arXiv. Early, dense, and occasionally six months ahead of the press",
    },
    "practitioner": {
        "title": "People actually using it",
        "weight": 1.25,
        "blurb": "Builders and engineers. Where a claim first meets a real workload",
    },
    "press": {
        "title": "Press",
        "weight": 0.85,
        "blurb": "Wide coverage, thin detail. Good for what a client will have read",
    },
    "money": {
        "title": "Money and strategy",
        "weight": 1.00,
        "blurb": "Where the funding goes, which is where the tooling goes next",
    },
    "security": {
        "title": "Security",
        "weight": 1.15,
        "blurb": "The one-page risk scan needs feeding. This is what feeds it",
    },
}

SOURCES: list[dict] = [
    # ---- the labs and the platforms -------------------------------------
    {"name": "OpenAI",            "band": "lab",
     "url": "https://openai.com/news/rss.xml"},
    {"name": "Google DeepMind",   "band": "lab",
     "url": "https://deepmind.google/blog/rss.xml"},
    {"name": "Google Research",   "band": "lab",
     "url": "https://research.google/blog/rss/"},
    {"name": "Hugging Face",      "band": "lab",
     "url": "https://huggingface.co/blog/feed.xml"},
    {"name": "AWS machine learning", "band": "lab",
     "url": "https://aws.amazon.com/blogs/machine-learning/feed/"},
    {"name": "Google Cloud AI",   "band": "lab",
     "url": "https://cloudblog.withgoogle.com/products/ai-machine-learning/rss/"},
    {"name": "NVIDIA",            "band": "lab",
     "url": "https://blogs.nvidia.com/feed/"},

    # ---- the record ------------------------------------------------------
    # Empty at weekends. arXiv does not announce on Saturday or Sunday, and the
    # collector reports that as "reachable, nothing in it" rather than a fault.
    {"name": "arXiv cs.AI",       "band": "record",
     "url": "https://export.arxiv.org/rss/cs.AI"},
    {"name": "arXiv cs.LG",       "band": "record",
     "url": "https://export.arxiv.org/rss/cs.LG"},
    {"name": "arXiv cs.CL",       "band": "record",
     "url": "https://export.arxiv.org/rss/cs.CL"},

    # ---- people who actually run this stuff ------------------------------
    {"name": "Simon Willison",    "band": "practitioner",
     "url": "https://simonwillison.net/atom/everything/"},
    {"name": "Latent Space",      "band": "practitioner",
     "url": "https://www.latent.space/feed"},
    {"name": "Import AI",         "band": "practitioner",
     "url": "https://importai.substack.com/feed"},
    {"name": "Hacker News — front page", "band": "practitioner",
     "url": "https://hnrss.org/frontpage?points=100"},
    {"name": "Hacker News — AI",  "band": "practitioner",
     "url": "https://hnrss.org/newest?q=%22AI%22&points=75"},
    {"name": "Hacker News — LLM", "band": "practitioner",
     "url": "https://hnrss.org/newest?q=LLM&points=50"},

    # ---- press -----------------------------------------------------------
    {"name": "TechCrunch AI",     "band": "press",
     "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
    {"name": "The Verge AI",      "band": "press",
     "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
    {"name": "VentureBeat AI",    "band": "press",
     "url": "https://venturebeat.com/category/ai/feed/"},
    {"name": "ZDNet AI",          "band": "press",
     "url": "https://www.zdnet.com/topic/artificial-intelligence/rss.xml"},
    {"name": "MIT Technology Review", "band": "press",
     "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed"},
    {"name": "Ars Technica",      "band": "press",
     "url": "https://feeds.arstechnica.com/arstechnica/technology-lab"},

    # ---- money -----------------------------------------------------------
    {"name": "Stratechery",       "band": "money",
     "url": "https://stratechery.com/feed/"},
    {"name": "Sequoia",           "band": "money",
     "url": "https://www.sequoiacap.com/feed/"},

    # ---- security --------------------------------------------------------
    {"name": "Krebs on Security", "band": "security",
     "url": "https://krebsonsecurity.com/feed/"},
    {"name": "Schneier on Security", "band": "security",
     "url": "https://www.schneier.com/feed/atom/"},
    {"name": "CISA advisories",   "band": "security",
     "url": "https://www.cisa.gov/cybersecurity-advisories/all.xml"},
]

# Tried on August 1, 2026 and not usable. Kept so nobody spends an evening
# rediscovering them. **Anthropic is the notable absence** --- it publishes no
# public RSS at all, so its announcements arrive here second-hand through
# Hacker News and the press.
DEAD_FEEDS = [
    ("Anthropic", "https://www.anthropic.com/rss.xml", "404 — no public feed exists"),
    ("Anthropic news", "https://www.anthropic.com/news/rss.xml", "404"),
    ("Meta AI", "https://ai.meta.com/blog/rss/", "404"),
    ("Mistral", "https://mistral.ai/news/feed.xml", "404"),
    ("Microsoft AI", "https://blogs.microsoft.com/ai/feed/", "410 Gone"),
    ("a16z", "https://a16z.com/feed/", "404"),
    ("Ben's Bites", "https://bensbites.beehiiv.com/feed", "404"),
    ("Hugging Face papers", "https://huggingface.co/papers/feed.xml", "401"),
]

# What matters to an advisor selling to owner-led SMBs of 10-50 people, rather
# than what matters to an AI researcher. A frontier benchmark result moves
# nothing in a 20-person HVAC company; the price of an API call does.
#
# Positive weights only --- this ranks, it does not filter. Nothing is hidden
# for failing to match, because the day a keyword list starts deciding what he
# is allowed to see is the day the radar starts confirming what he already
# thinks.
# A trailing `*` is a stem --- `agent*` catches agent, agents and agentic.
# Without one the match is a whole word on both sides. That is not fussiness:
# the first real run scored a story for "erp" because it contains ent**erp**rise,
# and another for "ban" because of Nano **Ban**ana. See `digest._compile_terms`.
#
# Each idea appears **once**. `price` and `pricing` as separate entries scored
# the same headline twice for saying one thing.
KEYWORDS: dict[str, float] = {
    # money, which is the whole conversation with an owner
    "pric*": 3.0, "cheaper": 2.5, "cost*": 2.0,
    "free tier": 2.5, "per token": 2.5, "open source": 2.0, "open-weight*": 2.0,
    # small business reality
    "small business*": 4.0, "smb": 4.0, "small and medium": 4.0,
    "contractor*": 3.0, "field service": 3.0, "dispatch*": 2.5,
    "scheduling": 2.5, "quoting": 2.5, "invoic*": 2.5, "inventory": 2.0,
    "manufactur*": 2.0, "distribution": 1.5, "hvac": 3.5, "trades": 2.5,
    # the work he actually sells
    "automat*": 2.5, "workflow*": 2.5, "agent*": 2.0, "assistant*": 1.5,
    "knowledge base*": 2.5, "rag": 1.5, "spreadsheet*": 2.0,
    "email": 1.5, "crm": 2.0, "erp": 2.0,
    # the differentiator underneath
    "data privacy": 3.0, "breach*": 3.0, "ransomware": 3.0, "leak*": 2.5,
    "compliance": 2.0, "on-premise*": 2.0, "on-prem": 2.0, "self-host*": 2.0,
    "vulnerabilit*": 2.0,
    # tools an owner might actually touch
    "microsoft 365": 3.0, "google workspace": 3.0, "excel": 2.5,
    "quickbooks": 3.0, "copilot": 2.5, "chatgpt": 2.0, "claude": 2.0,
    "gemini": 1.5, "llama": 1.5,
    # things that change what is possible
    "release*": 1.5, "launch*": 1.5, "general availability": 2.0,
    "deprecat*": 2.5, "shutting down": 3.0, "end of life": 3.0,
    "acquisition*": 2.0, "acquir*": 2.0, "funding": 1.0,
    "regulation*": 2.0, "lawsuit*": 1.5, "ban": 1.5, "bans": 1.5,
}

# Pushed down, never removed. Mostly research-internal vocabulary that is real
# work and simply not this business's work.
NOISE: dict[str, float] = {
    "benchmark*": -1.0, "sota": -1.5, "state-of-the-art": -1.5,
    "ablation*": -2.0, "we propose": -2.0, "novel framework": -2.0,
    "theorem*": -2.0, "convergence": -1.5, "gradient*": -1.5,
    "diffusion model*": -1.0, "transformer architecture": -1.0,
    "hiring": -1.5, "we're hiring": -2.5, "podcast*": -1.0,
    "webinar*": -1.5, "conference": -1.0, "keynote*": -1.0,
}


def by_band(band: str) -> list[dict]:
    return [s for s in SOURCES if s["band"] == band]
