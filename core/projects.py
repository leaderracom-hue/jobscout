"""Projects & contracts page helpers (28 Nov). Pure functions, no Streamlit, so pytest can cover them."""
import datetime as dt

# key -> (label, on by default, tab). Keys match SOURCES in engine/projects/find-projects.mjs. tab: "proj" or "buy".
SOURCES = {
    "n8n": ("n8n Community · Jobs", True, "proj"),
    "make": ("Make Community · Hire a Pro", True, "proj"),
    "clay": ("Clay Community · #share-jobs (GTM)", True, "proj"),
    "gtmjobs": ("GTM Engineer Jobs (בעיקר משרות מלאות)", False, "proj"),
    "hn": ("Hacker News · Who is hiring / Freelancer", True, "proj"),
    "himalayas": ("Himalayas · Contractor", True, "proj"),
    "braintrust": ("Braintrust", True, "proj"),
    "ajb": ("Automation Job Board", True, "proj"),
    "linkedin": ("LinkedIn Posts · מגייסים", False, "proj"),
    "reddit": ("Reddit · מגייסים", False, "proj"),
    "li_intent": ("LinkedIn Buy Intent", True, "buy"),
    "reddit_intent": ("Reddit Buy Intent", True, "buy"),
}
# Apify sources (prices checked 28 Nov, FREE tier): unit $ per item, start $ per run, default and cap on items, default queries.
PAID = {
    "linkedin": {"unit": 0.002, "start": 0, "max": 100, "cap": 300, "qlabel": "שאילתות חיפוש", "queries": [
        "looking for an n8n", "looking for a Make.com", "need a Zapier expert", "n8n expert needed", "recommend an n8n developer",
        "hiring freelance automation engineer", "contract AI agent developer", "automation specialists needed", "ISO n8n consultant",
        "help setting up HubSpot automation"]},
    "reddit": {"unit": 0.004, "start": 0, "max": 100, "cap": 300, "qlabel": "subreddits", "queries": ["forhire", "freelance_forhire", "n8n", "automation"]},
    "li_intent": {"unit": 0.002, "start": 0, "max": 100, "cap": 300, "qlabel": "שאילתות חיפוש", "queries": [
        "looking for automation agency", "can anyone recommend n8n expert", "need help automating our CRM", "looking for AI agent agency"]},
    "reddit_intent": {"unit": 0.002, "start": 0.02, "max": 50, "cap": 300, "qlabel": "שאילתות חיפוש", "queries": [
        '"automation agency" looking for', '"n8n" recommend expert', '"need help" automating workflow', '"AI agent" agency recommend']},
}


def cost(key, n):
    """Most a paid source can charge for n items (the engine never asks for more)."""
    c = PAID[key]
    return round(c["start"] + c["unit"] * n, 3)


def tab_of(p):
    """Which search tab a stored row belongs to."""
    return "buy" if p.get("engagement") == "Buy intent" else "proj"
KEEP_DAYS = 30
COLUMNS = ["fit", "title", "company", "source", "posted", "budget", "eligibility", "poster", "poster_profile", "url", "why"]


def _ts(iso):
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def merge(stored, items, now=None):
    """New engine items join the stored ones by id; a stored score, status and contact survive a re-run. Older than KEEP_DAYS is dropped."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = dict(stored or {})
    for it in items:
        old = out.get(it["id"]) or {}
        it = {**it, "desc": str(it.get("desc") or "")[:3000], "first_seen": old.get("first_seen") or now.isoformat()}  # scorer reads 3,000
        keep = ("fit", "why", "kind", "status") + (("poster",) if not (it.get("poster") or {}).get("name") else ())
        out[it["id"]] = {**it, **{k: old[k] for k in keep if k in old}}
    cutoff = now - dt.timedelta(days=KEEP_DAYS)
    return {k: v for k, v in out.items() if (_ts(v.get("posted")) or _ts(v.get("first_seen")) or now) >= cutoff}


def rows(projects):
    """Flat rows for the table and the CSV / Excel export. Missing data stays "לא נמצא", never guessed."""
    res = []
    for p in projects:
        po = p.get("poster") or {}
        res.append({"fit": p.get("fit"), "title": p.get("title") or "", "company": p.get("company") or "לא נמצא",
                    "source": p.get("source") or "", "posted": str(p.get("posted") or "")[:10], "budget": p.get("budget") or "לא צוין",
                    "eligibility": p.get("eligibility") or "", "poster": po.get("name") or "לא נמצא",
                    "poster_profile": po.get("profile") or "לא נמצא", "url": p.get("url") or "", "why": p.get("why") or ""})
    bad = ("=", "+", "-", "@", "\t", "\r")  # a cell starting with these runs as a formula in Excel
    return [{k: "'" + v if isinstance(v, str) and v.startswith(bad) else v for k, v in x.items()} for x in res]
