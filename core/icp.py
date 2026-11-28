"""ICP people search (28 Nov): LinkedIn profiles via Apify harvestapi/linkedin-profile-search. Pure helpers, no Streamlit."""
import math

ACTOR = "harvestapi~linkedin-profile-search"
PAGE = 25            # the actor bills per search page of 25 profiles
PAGE_USD = 0.10      # prices checked 28 Nov, FREE tier
MODES = {"Short": 0.0, "Full": 0.004, "Full + email search": 0.010}  # extra $ per profile on top of the page
HEADCOUNT = {"B": "1-10", "C": "11-50", "D": "51-200", "E": "201-500", "F": "501-1,000", "G": "1,001-5,000", "H": "5,001-10,000", "I": "10,001+"}
SENIORITY = {"310": "CXO", "320": "Owner / Partner", "300": "VP", "220": "Director", "210": "Experienced manager", "200": "Entry manager", "120": "Senior"}
COLUMNS = ["name", "headline", "company", "location", "profile", "email"]


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
                    "profile": _first(p.get("linkedinUrl"), p.get("url")) or "לא נמצא", "email": email or ""})
    bad = ("=", "+", "-", "@", "\t", "\r")  # Excel formula guard, as in core/projects.rows
    return [{k: "'" + v if isinstance(v, str) and v.startswith(bad) else v for k, v in x.items()} for x in res]
