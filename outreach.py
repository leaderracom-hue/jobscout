"""Direct outreach helpers (Assaf, 21 Nov 2026). No Streamlit here, so the functions are testable.

Email waterfall, each step only when the previous found nothing and ONLY on an explicit click:
  1. SmartProspect (Smartlead key): search is free, unlocking an email costs 1 credit
  2. Apollo (free account): people/match
  3. Apify email-finder actor (pnda/email-finder, ~$0.005 per verified email): last resort
Verification: MillionVerifier only. LeadMagic is off.
"""
import re
import time
import urllib.parse

import requests

SP = "https://prospect-api.smartlead.ai/api/v1/search-email-leads"
APIFY_ACTOR = "pnda~email-finder"
HR_TITLES = ["Talent Acquisition", "Recruiter", "HR Manager", "Head of People", "Hiring Manager"]
MAX_PER_HOUR = 12
GAP_SECONDS = 300


_SECRET_RX = re.compile(r"((?:api_key|token|api|key)=)[^&\s'\"]+", re.I)


def redact(text):
    """Strip key=... values from error text before it reaches the screen (SmartProspect and MillionVerifier only accept the key in the URL)."""
    return _SECRET_RX.sub(lambda m: m.group(1) + "***", str(text))


def split_name(full):
    p = re.sub(r"[^\w\s\-'.]", " ", full or "", flags=re.U).split()
    return (p[0], p[-1]) if len(p) >= 2 else ((p[0], "") if p else ("", ""))


def guess_domain(company):
    c = re.sub(r"\b(ltd|inc|llc|gmbh|corp|co|group|technologies|israel)\b\.?", "", (company or "").lower())
    c = re.sub(r"[^a-z0-9]", "", c)
    return f"{c}.com" if c else ""


# ---- 1. SmartProspect ----
def sp_search(key, company, domain, title_words=None, country=None, limit=25):
    """FREE. Returns people at the company (emails masked): name, title, deliverability."""
    body = {"limit": limit, "companyName": [company], "title": title_words or HR_TITLES}
    if country:
        body["country"] = [country]
    r = requests.post(f"{SP}/search-contacts", params={"api_key": key}, json=body, timeout=40)
    r.raise_for_status()
    d = r.json()
    dd = d.get("data") if isinstance(d.get("data"), dict) else d
    out = []
    for c in _sp_list(dd):
        site = str((c.get("company") or {}).get("website") or c.get("website") or "").lower()
        if domain and domain.lower() not in site:  # matching is loose, keep only the real domain
            continue
        out.append({"name": _sp_name(c), "title": c.get("title"), "deliv": c.get("emailDeliverability"), "raw_id": c.get("id")})
    return {"people": [p for p in out if p["name"]], "filter_id": dd.get("filter_id"), "total": dd.get("total_count")}


def _sp_list(dd):
    x = dd.get("list") or dd.get("contacts") or dd.get("data") or []
    return [c for c in x if isinstance(c, dict)]


def _sp_name(c):
    return c.get("fullName") or " ".join(filter(None, [c.get("firstName") or c.get("first_name"), c.get("lastName") or c.get("last_name")])) or c.get("name") or c.get("full_name") or ""


def sp_unlock(key, filter_id, name):
    """PAID: 1 credit per email. Fetches the contacts of the search and keeps only the one named `name`."""
    r = requests.post(f"{SP}/fetch-contacts", params={"api_key": key}, json={"filter_id": filter_id, "limit": 25, "visual_limit": 25}, timeout=60)
    r.raise_for_status()
    d = r.json()
    dd = d.get("data") if isinstance(d.get("data"), dict) else d
    for c in _sp_list(dd):
        nm = _sp_name(c)
        if nm.strip().lower() == (name or "").strip().lower() and c.get("email") and "random" not in c["email"]:
            return c["email"]
    return None


# ---- 2. Apollo ----
def apollo_match(key, name, domain):
    f, l = split_name(name)
    r = requests.post("https://api.apollo.io/api/v1/people/match", headers={"X-Api-Key": key, "Content-Type": "application/json"},
                      json={"first_name": f, "last_name": l, "domain": domain, "reveal_personal_emails": False}, timeout=40)
    if r.status_code != 200:
        raise RuntimeError(f"Apollo {r.status_code}: {r.text[:120]}")
    p = (r.json() or {}).get("person") or {}
    em = p.get("email")
    return em if em and "not_unlocked" not in em and "@" in em else None


# ---- 3. Apify (last alternative) ----
def apify_find(token, name, domain):
    f, l = split_name(name)
    r = requests.post(f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items", params={"timeout": 90}, headers={"Authorization": f"Bearer {token}"},
                      json={"persons": [{"firstName": f, "lastName": l, "domain": domain}]}, timeout=120)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Apify {r.status_code}: {r.text[:120]}")
    for it in r.json() or []:
        em = it.get("email") or it.get("emailAddress")
        if em and str(it.get("status", "found")).lower() in ("found", "valid", "ok"):
            return em
    return None


# ---- verification: MillionVerifier only ----
def mv_verify(key, email):
    r = requests.get("https://api.millionverifier.com/api/v3/", params={"api": key, "email": email}, timeout=40)
    r.raise_for_status()
    d = r.json()
    res = str(d.get("result") or "").lower()
    return {"result": res, "ok": res == "ok", "quality": d.get("quality"), "error": d.get("error")}


# ---- send helpers ----
def gmail_compose_url(to, subject, body):
    """Opens a compose window in the Gmail account he is signed into (his personal one). He presses Send."""
    q = urllib.parse.urlencode({"view": "cm", "fs": "1", "to": to, "su": subject, "body": body}, quote_via=urllib.parse.quote)
    return "https://mail.google.com/mail/?" + q


def pacing(sent_log, now=None):
    """sent_log: list of epoch seconds of emails he sent. Returns (ok, message)."""
    now = now or time.time()
    last_hour = [t for t in sent_log if now - t < 3600]
    if len(last_hour) >= MAX_PER_HOUR:
        return False, f"הגעת ל-{MAX_PER_HOUR} מיילים בשעה האחרונה. המתן."
    if last_hour and now - max(last_hour) < GAP_SECONDS:
        return False, f"עברו {int(now - max(last_hour))} שניות מהמייל האחרון. המתן {int(GAP_SECONDS - (now - max(last_hour)))} שניות (הפרש 5 דקות)."
    return True, ""
