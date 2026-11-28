"""ICP people search (28 Nov): LinkedIn profiles via Apify harvestapi/linkedin-profile-search. Pure helpers, no Streamlit."""
import math

ACTOR = "harvestapi~linkedin-profile-search"
PAGE = 25            # the actor bills per search page of 25 profiles
PAGE_USD = 0.10      # prices checked 28 Nov, FREE tier
MODES = {"Short": 0.0, "Full": 0.004, "Full + email search": 0.010}  # extra $ per profile on top of the page
HEADCOUNT = {"B": "1-10", "C": "11-50", "D": "51-200", "E": "201-500", "F": "501-1,000", "G": "1,001-5,000", "H": "5,001-10,000", "I": "10,001+"}
SENIORITY = {"310": "CXO", "320": "Owner / Partner", "300": "VP", "220": "Director", "210": "Experienced manager", "200": "Entry manager", "120": "Senior"}
COLUMNS = ["name", "headline", "company", "location", "industry", "deliverability", "website", "profile", "email", "source"]


def pages(n):
    return max(1, math.ceil(n / PAGE))


def cost(n, mode="Short"):
    """Most a run can charge: whole search pages plus the per-profile extra."""
    return round(pages(n) * PAGE_USD + n * MODES[mode], 3)


def build_input(icp, n, mode="Short"):
    lst = lambda k: [x.strip() for x in str(icp.get(k) or "").replace("\n", ",").split(",") if x.strip()][:10]
    out = {"profileScraperMode": mode, "maxItems": n, "takePages": pages(n)}
    if icp.get("query"):
        out["searchQuery"] = str(icp["query"]).strip()[:200]
    for k, field in (("titles", "currentJobTitles"), ("locations", "locations"), ("exclude_titles", "excludeCurrentJobTitles")):
        if lst(k):
            out[field] = lst(k)
    for k, field, allowed in (("headcount", "companyHeadcount", HEADCOUNT), ("seniority", "seniorityLevelIds", SENIORITY)):
        v = [x for x in (icp.get(k) or []) if x in allowed]
        if v:
            out[field] = v
    return out


def _first(*vals):
    for v in vals:
        if v:
            return v
    return ""


def rows(items):
    """Flat rows for the table and export. Field names vary by mode, so every read has a fallback; missing = 'לא נמצא'."""
    res = []
    for p in items or []:
        pos = (p.get("currentPositions") or p.get("currentPosition") or p.get("experience") or [{}])  # Short mode: currentPositions (checked 28 Nov)
        pos = pos[0] if isinstance(pos, list) and pos else (pos if isinstance(pos, dict) else {})
        loc = p.get("location")
        loc = loc.get("linkedinText") if isinstance(loc, dict) else loc
        emails = p.get("emails") or []
        email = _first(p.get("email"), (emails[0].get("email") if emails and isinstance(emails[0], dict) else (emails[0] if emails else "")))
        name = _first(p.get("name"), " ".join(x for x in (p.get("firstName"), p.get("lastName")) if x))
        res.append({"name": name or "לא נמצא", "headline": p.get("headline") or pos.get("title") or "",
                    "company": _first(pos.get("companyName"), p.get("companyName")) or "לא נמצא", "location": loc or "",
                    "industry": "", "deliverability": "", "website": "",
                    "profile": _first(p.get("linkedinUrl"), p.get("url")) or "לא נמצא", "email": email or "", "source": "LinkedIn · Apify"})
    bad = ("=", "+", "-", "@", "\t", "\r")  # Excel formula guard, as in core/projects.rows
    return [{k: "'" + v if isinstance(v, str) and v.startswith(bad) else v for k, v in x.items()} for x in res]


# ---- SmartProspect (Smartlead): search is FREE, emails and LinkedIn stay masked until a paid unlock (1 credit) ----
SP_URL = "https://prospect-api.smartlead.ai/api/v1/search-email-leads/search-contacts"
SP_HEADERS = {"User-Agent": "curl/8.4.0"}  # Cloudflare answers 403 to the default python UA
# shortcut: LinkedIn headcount codes mapped to the nearest SmartProspect bands, so one form drives both sources.
SP_HEADCOUNT = {"B": ["0 - 25"], "C": ["0 - 25", "25 - 100"], "D": ["25 - 100", "100 - 250"], "E": ["100 - 250", "250 - 1000"],
                "F": ["250 - 1000"], "G": ["1K - 10K"], "H": ["1K - 10K"], "I": ["10K - 50K"]}


def sp_body(icp, n):
    lst = lambda k: [x.strip() for x in str(icp.get(k) or "").replace("\n", ",").split(",") if x.strip()][:10]
    body = {"limit": max(1, min(500, n)), "dontDisplayOwnedContact": False}
    if lst("titles"):
        body["title"] = lst("titles")
    if lst("locations"):
        body["country"] = lst("locations")
    bands = sorted({b for c in (icp.get("headcount") or []) for b in SP_HEADCOUNT.get(c, [])})
    if bands:
        body["companyHeadCount"] = bands
    return body


def sp_rows(items, icp=None):
    """SmartProspect has no working industry filter, so keywords and excluded titles are applied here, after the free search."""
    icp = icp or {}
    words = [w.strip().lower() for w in str(icp.get("query") or "").split(",") if w.strip()]
    ex = [w.strip().lower() for w in str(icp.get("exclude_titles") or "").split(",") if w.strip()]
    res = []
    for c in items or []:
        co = c.get("company") or {}
        hay = f"{c.get('industry') or ''} {c.get('subIndustry') or ''} {co.get('name') or ''}".lower()
        if words and not any(w in hay for w in words):
            continue
        if any(w in str(c.get("title") or "").lower() for w in ex):
            continue
        res.append({"name": c.get("fullName") or " ".join(x for x in (c.get("firstName"), c.get("lastName")) if x) or "לא נמצא",
                    "headline": c.get("title") or "", "company": co.get("name") or "לא נמצא",
                    "location": ", ".join(x for x in (c.get("city"), c.get("country")) if x),
                    "industry": " / ".join(x for x in (c.get("industry"), c.get("subIndustry")) if x),
                    "deliverability": c.get("emailDeliverability") or 0, "website": co.get("website") or "",
                    "profile": "", "email": "", "source": "SmartProspect", "sp_id": c.get("id") or ""})
    bad = ("=", "+", "-", "@", "\t", "\r")
    return [{k: "'" + v if isinstance(v, str) and v.startswith(bad) else v for k, v in x.items()} for x in res]
