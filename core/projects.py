"""Projects & contracts page helpers (28 Nov). Pure functions, no Streamlit, so pytest can cover them."""
import datetime as dt

# key -> (label, on by default). Keys match SOURCES in engine/projects/find-projects.mjs.
SOURCES = {
    "n8n": ("n8n Community · Jobs", True),
    "make": ("Make Community · Hire a Pro", True),
    "reddit": ("Reddit · r/forhire, r/n8n ועוד · Apify בתשלום (~$0.40 להרצה)", False),
    "linkedin": ("LinkedIn Posts · Apify בתשלום (~$0.16 להרצה)", False),
    "clay": ("Clay Community · #share-jobs (GTM)", True),
    "gtmjobs": ("GTM Engineer Jobs (בעיקר משרות מלאות)", False),
    "hn": ("Hacker News · Who is hiring / Freelancer", True),
    "himalayas": ("Himalayas · Contractor", True),
    "braintrust": ("Braintrust", True),
    "ajb": ("Automation Job Board", True),
}
PAID = {"linkedin": 0.16, "reddit": 0.40}  # Apify sources: $ per run, confirmed by a click before every run
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
