"""Job Scout (cloud) — pick a keyword, search the Israeli job boards on click, rank each job and pick the best CV.
Sent / Remove move a job to the archive, and archived jobs never come back.

Code is public; everything private lives elsewhere:
  * secrets (Streamlit → Settings → Secrets): APP_PASSWORD, APIFY_API_TOKEN, ANTHROPIC_API_KEY, GH_DATA_TOKEN, GH_DATA_REPO, MAX_RUNS_PER_DAY
  * data (private GitHub repo GH_DATA_REPO): state.json + cv/en/*.pdf + cv/he/*.pdf
Searching runs engine/mini-agent/scout.mjs (Node) — the same code as the local engine.
"""
import base64
import datetime as dt
import hmac
from html import escape as E  # Assaf 25 Nov: job and model text is untrusted; escape it before unsafe_allow_html
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

APP = Path(__file__).parent
IL = ZoneInfo("Asia/Jerusalem")
GH = "https://api.github.com"
# Assaf 19 Nov: dropped ai trainer, freelancer (gtm ai restored: GTM roles always stay) (no job ever scored 40+); integration / product erp were already gone
KEYWORDS = ["product ai", "marketing automation", "product marketing", "gtm product", "gtm ai", "gtm engineer", "ai builder", "ai automation",
            "automation", "crm integration", "crm automation", "מנהל מוצר", "מנהל מוצר אוטומציות", "אוטומציות שיווק", "מנהל פרויקט אוטומציות",
            # Assaf 18 Nov: 12 more
            "מיישם פתרונות ai", "ai implementation", "ai solutions", "ai product owner", "technical product manager", "data product manager",
            "product operations", "crm product", "lifecycle marketing", "טרנספורמציה דיגיטלית", "מנהל חדשנות", "מנהל מערכות שיווק",
            # Assaf 24 Nov: 4 more
            "gtm automation", "hubspot", "campaign manager", "media buyer", "sales ai solution",
            "ai sales", "ai solutions sales", "מנהל מכירות פתרונות ai", "account executive ai", "business development ai", "solutions consultant ai",
            "workflow ai", "customer success manager",
            "נציג שירות מהבית", "עבודה מהבית", "remote customer support", "נציג שימור",
            "ai trainer", "n8n freelance", "crm setup freelance",
            # Assaf 24 Nov: 2 more
            "lead generation", "appointment setter"]
LABEL = {"product ai": "Product AI", "marketing automation": "Marketing Automation", "product marketing": "Product Marketing",
         "gtm product": "GTM Product", "gtm ai": "GTM AI", "gtm engineer": "GTM Engineer", "ai builder": "AI Builder", "ai automation": "AI Automation", "automation": "Automation",
         "crm integration": "CRM Integration", "crm automation": "CRM Automation", "ai trainer": "AI Trainer", "freelancer": "Freelancer", "מנהל מוצר": "מנהל מוצר", "מנהל מוצר אוטומציות": "מנהל מוצר אוטומציות", "אוטומציות שיווק": "אוטומציות שיווק", "מנהל פרויקט אוטומציות": "מנהל פרויקט אוטומציות",
         "מיישם פתרונות ai": "מיישם פתרונות AI", "ai implementation": "AI Implementation", "ai solutions": "AI Solutions", "ai product owner": "AI Product Owner",
         "technical product manager": "Technical Product Manager", "data product manager": "Data Product Manager", "product operations": "Product Operations",
         "crm product": "CRM / Salesforce Product", "lifecycle marketing": "Lifecycle / Retention", "טרנספורמציה דיגיטלית": "טרנספורמציה דיגיטלית",
         "מנהל חדשנות": "מנהל חדשנות", "מנהל מערכות שיווק": "מנהל מערכות שיווק",
         "gtm automation": "GTM Automation", "hubspot": "HubSpot", "campaign manager": "Campaign Manager", "media buyer": "Media Buyer", "sales ai solution": "Sales AI Solution", "ai sales": "AI Sales", "ai solutions sales": "AI Solutions Sales",
         "מנהל מכירות פתרונות ai": "מנהל מכירות פתרונות AI", "account executive ai": "Account Executive AI", "business development ai": "Business Development AI",
         "solutions consultant ai": "Solutions Consultant AI", "workflow ai": "Workflow AI", "customer success manager": "Customer Success Manager", "נציג שירות מהבית": "נציג שירות מהבית", "עבודה מהבית": "עבודה מהבית",
         "remote customer support": "Remote Customer Support", "נציג שימור": "נציג שימור", "n8n freelance": "n8n Freelance", "crm setup freelance": "CRM Setup Freelance",
         "lead generation": "Lead Generation", "appointment setter": "Appointment Setter"}
WINDOWS = {24: "24 שעות", 48: "48 שעות", 72: "72 שעות", 0: "שבוע"}
MIN_FIT = 40  # Assaf 19 Nov: threshold lowered 45 -> 40; a score under it goes straight to the archive ("auto") and is never shown
HISTORY_HOURS = 72  # Assaf: past searches can be reopened for 72 hours
BOARD = {"linkedin": "LinkedIn", "indeed": "Indeed", "drushim": "דרושים", "alljobs": "AllJobs", "jobmaster": "JobMaster", "careers": "דף קריירה",
         "himalayas": "Himalayas"}

# Assaf 21 Nov: pastel palette, ~75% blue / 25% green, set once here and used everywhere (CSS variables + chart colors)
PAL = {"blue": "#8EBCE6", "blue_l": "#DCECF9", "green": "#A8D8C0", "green_l": "#E1F2E8",
       "blue_ink": "#1d4f7f", "green_ink": "#1f5e43", "ink": "#12324f"}
st.set_page_config(page_title="Job Scout", page_icon=":material/work:", layout="wide")
st.markdown("""<style>
:root{--js-blue:#8EBCE6;--js-blue-l:#DCECF9;--js-green:#A8D8C0;--js-green-l:#E1F2E8;--js-blue-ink:#1d4f7f;--js-green-ink:#1f5e43;--js-ink:#12324f}
button[kind="primary"],button[kind="primaryFormSubmit"],[data-testid="stBaseButton-primary"],[data-testid="stBaseButton-primaryFormSubmit"]{color:var(--js-ink) !important;font-weight:600}
[data-testid="stBaseButton-primary"]:hover{background:var(--js-green) !important;border-color:var(--js-green) !important}
[data-testid="stBaseButton-pillsActive"],[data-testid="stBaseButton-segmented_controlActive"]{background:var(--js-green-l) !important;border-color:var(--js-green) !important;color:var(--js-green-ink) !important}
.subfit{font-size:.68rem;color:#4b5563;text-align:center;margin-top:4px;line-height:1.25}
.t-warn{color:#8a5a00;border-color:#f0d9a8;background:#fff8e8}.t-no{color:#5b6474;border-color:#dde1e8;background:#eef0f3}
.block-container{max-width:1150px;padding-top:3.8rem}
.stMainBlockContainer, .stMarkdown, .stCaption, [data-testid="stExpander"], [data-testid="stSidebarContent"]{direction:rtl;text-align:right}
[data-testid="stMetric"]{background:var(--js-blue-l);border:1px solid var(--js-blue);border-radius:12px;padding:10px 14px}
[data-testid="stMetricValue"]{font-size:1.6rem}
.brand{font-weight:700;font-size:1.35rem;margin:-6px 0 10px}.brand small{display:block;font-weight:400;font-size:.78rem;color:#6b7280}
.side-kpi{display:flex;justify-content:space-between;padding:5px 2px;border-bottom:1px solid #eef0f3;font-size:.88rem}.side-kpi b{font-weight:600}
.fit{width:54px;height:54px;border-radius:12px;display:flex;flex-direction:column;align-items:center;justify-content:center;
     font-family:ui-monospace,monospace;font-weight:700;font-size:1.25rem;line-height:1}
.fit small{font-size:.58rem;font-weight:500;margin-top:3px;font-family:sans-serif}
.f-hi{background:var(--js-green-l);color:var(--js-green-ink)}.f-mid{background:var(--js-blue-l);color:var(--js-blue-ink)}.f-lo{background:#eef3f8;color:#4b6a88}.f-na{background:#eef0f3;color:#5b6474}
.jt{font-weight:600;font-size:1.05rem;line-height:1.35}.jt a{color:inherit;text-decoration:none}.jt a:hover{color:var(--js-blue-ink)}
.sub{color:#6b7280;font-size:.86rem;margin-top:1px}
.tags{margin-top:4px}
.tag{display:inline-block;font-size:.72rem;border:1px solid #dde1e8;border-radius:999px;padding:0 9px;margin:2px 0 0 4px;color:#5b6474;background:#fff}
.t-new{background:var(--js-blue);border-color:var(--js-blue);color:var(--js-ink)}.t-kw{color:var(--js-blue-ink);border-color:var(--js-blue);background:var(--js-blue-l)}
.t-ok{color:var(--js-green-ink);border-color:var(--js-green);background:var(--js-green-l)}.t-cv{color:var(--js-blue-ink);border-color:var(--js-blue);background:var(--js-blue-l);font-family:ui-monospace,monospace}
details.why{margin-top:5px;font-size:.88rem;color:#374151}details.why summary{cursor:pointer;color:var(--js-blue-ink);font-size:.8rem;list-style:none}
details.why summary::before{content:"▸ "}details.why[open] summary::before{content:"▾ "}
.t-star{color:#9a5b00;border-color:#f0d9a8;background:#fff8e8}
.warn{display:inline-block;margin-top:4px;font-size:.8rem;color:#9a5b00;background:#fff8e8;border:1px solid #f0d9a8;border-radius:6px;padding:1px 8px}
.ai-h{font-weight:600;color:var(--js-blue-ink)}.ai-a{background:var(--js-blue-l);border:1px solid var(--js-blue);border-radius:10px;padding:8px 12px;margin:4px 0 10px;white-space:pre-wrap}
</style>""", unsafe_allow_html=True)


try:  # read once in the script thread; background search threads use this copy
    SECRETS = {k: st.secrets[k] for k in st.secrets}
except Exception:
    SECRETS = {}


def secret(name, default=None):
    return SECRETS.get(name, default)


@st.cache_resource
def _shared():
    """One per server process, shared by every rerun and every background search thread."""
    return {"lock": threading.RLock(), "latest": None, "batches": {}}


SH = _shared()


# ---------------- password ----------------
def gate():
    if st.session_state.get("ok"):
        return
    st.title("Job Scout")
    with st.form("login"):
        pw = st.text_input("סיסמה", type="password")
        if st.form_submit_button("כניסה", type="primary"):
            if hmac.compare_digest(pw.encode(), str(secret("APP_PASSWORD", "")).encode()) and secret("APP_PASSWORD"):
                st.session_state.ok = True
                st.rerun()
            time.sleep(1.5)  # slows down guessing
            st.info("סיסמה שגויה", icon="⚠️")
    st.stop()


gate()


# ---------------- private data repo ----------------
def gh_headers(raw=False):
    return {"Authorization": f"Bearer {secret('GH_DATA_TOKEN')}", "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def gh_url(path):
    return f"{GH}/repos/{secret('GH_DATA_REPO')}/contents/{path}"


READ_TTL = 45  # seconds a downloaded copy of state.json is reused (writes from this server are seen at once via SH["latest"])


def load_state():
    # GitHub may serve a read up to ~60 s old right after a write, so the copy this server last wrote wins for 90 s.
    # 26 Nov: plus a shared read cache of READ_TTL s, so page switches and reruns stop downloading the 5.6 MB file every time.
    c = SH["latest"]
    if c and time.time() - c[2] < 90:
        return json.loads(json.dumps(c[0])), c[1]
    c = SH.get("read")
    if c and time.time() - c[2] < READ_TTL:
        return json.loads(json.dumps(c[0])), c[1]
    r = requests.get(gh_url("state.json"), headers=gh_headers(), timeout=20)
    if r.status_code == 404:
        return {"jobs": {}, "archive": {}, "runs": []}, None
    r.raise_for_status()
    meta = r.json()
    raw = base64.b64decode(meta["content"]) if meta.get("content") else requests.get(gh_url("state.json"), headers=gh_headers(True), timeout=30).content
    s = json.loads(raw)
    SH["read"] = (s, meta["sha"], time.time())
    return json.loads(json.dumps(s)), meta["sha"]


def profile():
    """Phase 1 (25 Nov): the job seeker's facts (profile.json in the data repo + settings.profile in state.json) - every prompt, the CV PDF and the
    screens read the candidate from here instead of from the code. Cached 10 minutes, safe in background threads."""
    c = SH.get("profile_cache")
    if c and time.time() - c[0] < 600:
        return c[1]
    p = {}
    try:
        r = requests.get(gh_url("profile.json"), headers=gh_headers(True), timeout=20)
        if r.ok:
            p.update(r.json())
    except Exception:
        pass
    try:  # fields edited in the app (settings.profile in state.json) win over profile.json
        p.update((load_state()[0].get("settings") or {}).get("profile") or {})
    except Exception:
        pass
    SH["profile_cache"] = (time.time(), p)
    return p


def ats_extra_load():
    """Careers boards the engine discovered on its own, kept in the private repo (ats-extra.json)."""
    r = requests.get(gh_url("ats-extra.json"), headers=gh_headers(), timeout=20)
    if r.status_code == 404:
        return [], None
    r.raise_for_status()
    m = r.json()
    return json.loads(base64.b64decode(m["content"])), m["sha"]


def ats_extra_add(found):
    if not found:
        return
    for _ in range(3):
        cur, sha = ats_extra_load()
        have = {(c["ats"], c["slug"]) for c in cur}
        new = [f for f in found if (f["ats"], f["slug"]) not in have]
        if not new:
            return
        body = {"message": f"careers list +{len(new)}", "content": base64.b64encode(json.dumps(cur + new, ensure_ascii=False, indent=1).encode()).decode()}
        if sha:
            body["sha"] = sha
        if requests.put(gh_url("ats-extra.json"), headers=gh_headers(), json=body, timeout=30).status_code in (200, 201):
            return
        time.sleep(0.8)


def update_state(mutate, message):
    """Read, apply the change, write back. One writer at a time on this server (lock); retry if GitHub saw another write.
    25 Nov: GitHub returned 500 on every write for ~15 minutes. Server errors are now retried with growing waits (about 45 s in
    all), and if the save still fails the user sees a plain message instead of a crash (nothing was saved; try again)."""
    last = None
    with SH["lock"]:
        for attempt in range(8):
            s, sha = load_state()
            mutate(s)
            body = {"message": message, "content": base64.b64encode(json.dumps(s, ensure_ascii=False, separators=(",", ":")).encode()).decode()}
            if sha:
                body["sha"] = sha
            try:
                r = requests.put(gh_url("state.json"), headers=gh_headers(), json=body, timeout=60)
            except requests.RequestException as e:
                r, last = None, e
            if r is not None and r.status_code in (200, 201):
                SH["latest"] = (s, r.json()["content"]["sha"], time.time())
                return s
            SH["latest"] = None
            if r is not None:
                last = r.status_code
                if r.status_code not in (409, 422) and r.status_code < 500:
                    r.raise_for_status()
            time.sleep(min(1.5 * (attempt + 1), 8))
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        on_screen = get_script_run_ctx() is not None
    except Exception:
        on_screen = False
    if on_screen:  # a click on the screen: explain and stop this run, the page stays usable
        st.error(f"השמירה נכשלה: GitHub, שבו נשמרים הנתונים, לא זמין כרגע ({last}). שום דבר לא השתנה. נסה שוב בעוד דקה.")
        st.stop()
    raise RuntimeError(f"could not save to the data repo ({last})")


@st.cache_data(ttl=600, show_spinner=False)
def cv_files(lang):
    r = requests.get(gh_url(f"cv/{lang}"), headers=gh_headers(), timeout=20)
    return sorted(x["name"] for x in r.json() if x["name"].lower().endswith(".pdf")) if r.ok else []


@st.cache_data(ttl=3600, show_spinner=False)
def cv_bytes(lang, name):
    r = requests.get(gh_url(f"cv/{lang}/{requests.utils.quote(name)}"), headers=gh_headers(True), timeout=60)
    r.raise_for_status()
    return r.content


# ---------------- search + scoring ----------------
DICT = json.loads((APP / "engine/mini-agent/dictionary.json").read_text(encoding="utf-8"))



from core import profile as PF  # noqa: E402
from core.rules import (  # noqa: E402  moved out of app.py 25 Nov (phase 1)
    LOC_ALL_EU, LOC_IL, LOC_REMOTE, ARR_HE, CYBER_CO, CYBER_TI, DICT, ELIG_HE, EMP_HE, EN_WORDS, EU_COUNTRIES, EU_RX, GTM_RX, IL_PLACES, ISRAELI_BOARDS, MED_CO, MED_TI, OTHER_WORDS, _list_rx, _word_hit, apply_prefs, company_key, excluded, final_score, intl_reason, is_cyber, is_english_post, is_gtm, is_international, is_medical, job_countries, key_of, lang_of, loc_match, norm, settings_of)


BRAIN = (APP / "engine/config/match-brain.md").read_text(encoding="utf-8")


def cv_pool(kw, lang):
    """Assaf 24 Nov: ALL CVs of the language are candidates (the old per-keyword list never let a new CV win); the CV brain decides."""
    have = cv_files(lang)
    return [f for f in have if not re.search(r"cover|customer.?support|appointment|admin", f, re.I)]


@st.cache_data(ttl=600, show_spinner=False)
def cv_brain():
    """cv/brain.json in the data repo: one profile per CV (positioning, target roles, ATS keywords, proof, choose_when, weak_for) + routing rules."""
    try:
        r = requests.get(gh_url("cv/brain.json"), headers=gh_headers(True), timeout=30)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def brain_block(lang, files):
    """Compact catalog of the CVs in `files`, for the prompts that choose a CV."""
    b = cv_brain()
    if not b:
        return ""
    out = [b.get("rules", ""), "", "CV CATALOG (choose cv_file only from here):"]
    for f in files:
        p = (b.get("profiles") or {}).get(f"{lang}/{f}")
        if not p:
            out.append(f"- {f}")
            continue
        out.append(f"- {f} [{p['family']}] {p['positioning']} | roles: {'; '.join(p['target_roles'][:6])} | keywords: {', '.join(p['ats_keywords'][:12])} | "
                   f"tools: {', '.join(p['key_tools'][:8])} | choose when: {p['choose_when']} | weak for: {p['weak_for']}"
                   + (f" | near duplicate of: {', '.join(p['near_duplicates'])}" if p.get("near_duplicates") else ""))
    return "\n".join(out)


EMP_TYPES = ["Full-Time", "Part-Time", "Contract", "Freelance"]
ARRANGEMENTS = ["Remote", "Hybrid", "On-site"]
REGION_OPTS = {"IL": "ישראל", "EEA": "EEA / אירופה"}
MODEL = "claude-haiku-5-5"  # Assaf 21 Nov: the cheapest model for EVERYTHING in the app (saves tokens)
SCORE_MODEL = TIE_MODEL = MODEL
B_BAND = (25, 100)  # 25 Nov: second review skipped only for clear rejects; a single review ran ~10 points high on good jobs  # 2 independent reviews; a third decides gaps > 10
PRICE = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0)}  # $ per 1M tokens in / out

EVAL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["requirements", "employment_type", "work_arrangement", "location", "languages_required", "professional_match", "eligibility_status",
                 "eligibility_match", "verdict", "why_he", "gaps", "cv_file"],
    "properties": {
        "requirements": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                         "required": ["requirement", "kind", "status", "evidence"],
                         "properties": {"requirement": {"type": "string"}, "kind": {"type": "string", "enum": ["must", "nice"]},
                                        "status": {"type": "string", "enum": ["met", "partial", "missing", "unknown"]},
                                        "evidence": {"type": "string"}}}},
        "employment_type": {"type": "object", "additionalProperties": False, "required": ["value", "evidence"],
                            "properties": {"value": {"type": "string", "enum": EMP_TYPES + ["Unknown"]}, "evidence": {"type": "string"}}},
        "work_arrangement": {"type": "object", "additionalProperties": False, "required": ["value", "evidence"],
                             "properties": {"value": {"type": "string", "enum": ARRANGEMENTS + ["Unknown"]}, "evidence": {"type": "string"}}},
        "location": {"type": "object", "additionalProperties": False, "required": ["job_region", "allowed_work_from", "residency_or_permit", "evidence"],
                     "properties": {"job_region": {"type": "string", "enum": ["Israel", "EEA", "Other", "Global", "Unknown"]},
                                    "allowed_work_from": {"type": "string"}, "residency_or_permit": {"type": "string"},
                                    "evidence": {"type": "string"}}},
        "languages_required": {"type": "array", "items": {"type": "string"}},
        "professional_match": {"type": "integer"},
        "eligibility_status": {"type": "string", "enum": ["eligible", "needs_verification", "not_eligible"]},
        "eligibility_match": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["Apply now", "Apply", "Maybe", "Skip"]},
        "why_he": {"type": "string"},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "cv_file": {"type": "string"},
    },
}
TIE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["disagreement", "professional_match", "eligibility_status", "eligibility_match", "verdict", "why_he", "gaps"],
    "properties": {"disagreement": {"type": "string"}, "professional_match": {"type": "integer"},
                   "eligibility_status": {"type": "string", "enum": ["eligible", "needs_verification", "not_eligible"]},
                   "eligibility_match": {"type": "integer"}, "verdict": {"type": "string", "enum": ["Apply now", "Apply", "Maybe", "Skip"]},
                   "why_he": {"type": "string"}, "gaps": {"type": "array", "items": {"type": "string"}}},
}
RUBRIC = """
## Scoring method (documented, the same for every review)
{ELIGIBILITY}

1. requirements: list every requirement the POST states (max 12). kind = must (stated as required / must / X+ years) or nice (advantage / plus / preferred). status: met = the profile proves it; partial = related but weaker; missing = the post requires it and the profile proves he does not have it; unknown = the profile does not say. Evidence = a short quote from the post or the profile. A requirement the post does not state is NOT missing.
2. professional_match 0-100 (skills, role, systems, experience only; ignore location and contract terms): start from role fit (same function and seniority = 70, adjacent = 50, different function = 20), then +up to 25 for met must-haves and nice-to-haves, -10 for each missing must-have (not for unknown), -5 per partial must-have. Cyber / information-security employer or role = 0.
3. employment_type and work_arrangement: only what the post says (quote it). Not stated = Unknown. Never assume.
4. languages_required: every human language the POST requires the worker to speak or write (in English, e.g. "English", "German"). Empty list if none is stated. "Nice to have" languages do not count.
   location: job_region of the role itself; allowed_work_from = where the post allows the worker to be (or "not stated"); residency_or_permit = any residency / work-permit / time-zone limit (or "none stated").
5. eligibility_status: not_eligible only when the post PROVES a hard conflict (e.g. must live in the US, on-site in Berlin with no relocation, local citizenship he lacks). needs_verification when something material is not stated. eligible when terms are clear and fit his facts. eligibility_match 0-100 follows: eligible 85-100, needs_verification 50-75, not_eligible 0-25.
6. verdict follows professional_match and eligibility: Apply now (80+ and not not_eligible), Apply (60-79), Maybe (40-59), Skip (<40 or not_eligible).
7. why_he: ONE short sentence in simple, natural Hebrew (max 18 words), male second person ("יש לך…"): the main reason it fits, then the main gap after "פער:". Tool names in English. Never state how many years or months he has. Hebrew letters, English and digits only.
8. gaps: up to 4 short Hebrew items. cv_file: copy exactly one file name from the CV CATALOG. Pick it by the rules in the catalog: the job's function decides the family, then the CV whose target roles and keywords overlap most with the post's title and stated requirements; never a CV whose weak_for matches this post.
"""


def rubric():
    return RUBRIC.replace("{ELIGIBILITY}", PF.eligibility_facts(profile()))


def track(r):
    """Count the tokens of one Claude reply (thread-safe, in memory); flush_usage() saves them to the data repo per month."""
    u = getattr(r, "usage", None)
    if not u:
        return
    with SH["lock"]:
        p = SH.setdefault("usage_pending", {"in": 0, "out": 0, "calls": 0})
        p["in"] += (u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
        p["out"] += u.output_tokens or 0
        p["calls"] += 1


def flush_usage(force=False):
    p = SH.get("usage_pending")
    if not p or not p["calls"]:
        return
    if not force and time.time() - SH.get("usage_flush", 0) < 60:
        return
    with SH["lock"]:
        add, SH["usage_pending"] = dict(p), {"in": 0, "out": 0, "calls": 0}
        SH["usage_flush"] = time.time()
    month = dt.datetime.now(IL).strftime("%Y-%m")

    def m(s):
        u = s.setdefault("usage", {}).setdefault(month, {"in": 0, "out": 0, "calls": 0})
        for k in ("in", "out", "calls"):
            u[k] += add[k]
    try:
        update_state(m, "usage")
    except Exception:
        with SH["lock"]:  # keep it for the next flush
            q = SH["usage_pending"]
            for k in ("in", "out", "calls"):
                q[k] += add[k]


def _redact(e):
    """Error text for the screen and the saved state, with any key=... value masked."""
    from outreach import redact
    return redact(str(e))


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"), max_retries=4)


def _call(model, system, user, schema, effort="low"):
    """Assaf 25 Nov (cost): scoring runs at effort "low" (the model thinks less; output tokens were ~85% of the bill)."""
    r = _client().messages.create(model=model, max_tokens=16000, system=system, messages=[{"role": "user", "content": user}],
                                  output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}})
    track(r)
    if r.stop_reason == "refusal":
        raise RuntimeError("refused")
    if r.stop_reason == "max_tokens":
        raise RuntimeError("the reply was cut at max_tokens")
    text = next((b.text for b in r.content if getattr(b, "type", "") == "text"), "")
    if not text.strip():
        raise RuntimeError("empty reply")
    pi, po = PRICE.get(model, (1, 5))
    return json.loads(text), r.usage.input_tokens / 1e6 * pi + r.usage.output_tokens / 1e6 * po


def _json_reply(r):
    """26 Nov: a reply cut at max_tokens or with no text block raises a clear error instead of StopIteration / JSONDecodeError."""
    if r.stop_reason == "refusal":
        raise RuntimeError("Claude סירב לבקשה")
    if r.stop_reason == "max_tokens":
        raise RuntimeError("התשובה של Claude נקטעה באמצע. נסה שוב")
    t = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
    if not t:
        raise RuntimeError("Claude לא החזיר טקסט. נסה שוב")
    return json.loads(t)


def _post_text(j, pool):
    return "\n".join([
        f"TODAY'S DATE: {dt.date.today()}", f"TITLE: {j['title']}", f"COMPANY: {j.get('company') or 'not stated'}",
        f"LOCATION ON THE POST: {j.get('location') or 'not stated'}", f"SOURCE FIELDS: workplace={j.get('workplace') or 'n/a'}", "",
        "FULL DESCRIPTION:", str(j.get("desc") or "")[:6000] or "(the post has no description text; judge from the title and company, most fields are Unknown)",
        "", f"CV FILES TO CHOOSE FROM: {json.dumps(pool, ensure_ascii=False)}"])


def score_job(j, kw, prefs=None):
    """Assaf 21 Nov: two independent reviews with the same documented rubric + a tie-break when they differ by more than 10."""
    lang = lang_of(j)
    pool = cv_pool(kw, lang)
    system = [{"type": "text", "text": BRAIN + "\n" + rubric() + "\n\n" + brain_block(lang, pool), "cache_control": {"type": "ephemeral"}}]
    post = _post_text(j, pool)
    a_user = post + "\n\nReview this post against the candidate with the scoring method. Return the JSON."
    b_user = ("Independent second review. Do not assume any earlier score. Re-read the post from the top: first list every requirement "
              "and decide whether each is truly a must-have or only nice-to-have; check for requirements a quick reading would over- or "
              "under-weight; then score with the same method.\n\n" + post)
    a, usd = _call(SCORE_MODEL, system, a_user, EVAL_SCHEMA)
    # Assaf 25 Nov (cost): the second review runs only where it can change the decision - near the threshold (MIN_FIT) and in the
    # middle band. A clear reject (< 25) keeps review A alone; good jobs keep both reviews (one review alone scored them ~10 points high in the 25 Nov test).
    if B_BAND[0] <= a["professional_match"] <= B_BAND[1]:
        b, ub = _call(SCORE_MODEL, system, b_user, EVAL_SCHEMA)
        usd += ub
    else:
        b = a
    tie = None
    if b is not a and abs(a["professional_match"] - b["professional_match"]) > 10 or abs(a["eligibility_match"] - b["eligibility_match"]) > 10:
        t_user = (post + "\n\nTwo independent reviews of this post disagree by more than 10 points. REVIEW A:\n" + json.dumps(a, ensure_ascii=False)
                  + "\n\nREVIEW B:\n" + json.dumps(b, ensure_ascii=False)
                  + "\n\nExplain in Hebrew (disagreement, max 2 sentences) which requirements caused the gap and which reading the post supports. "
                    "Decide the scores yourself from the post and the verified candidate facts, using the same method. Do not average.")
        tie, ut = _call(TIE_MODEL, system, t_user, TIE_SCHEMA, effort="medium")
        usd += ut
    base = a  # requirement list + extracted terms come from review A; the decision fields from the tie-break when there was one
    dec = tie or {k: round((a[k] + b[k]) / 2) if k in ("professional_match", "eligibility_match") else a[k]
                  for k in ("professional_match", "eligibility_match", "eligibility_status", "verdict", "why_he", "gaps")}
    if not tie:  # close reviews: keep A's wording, take the stricter eligibility
        order = ["not_eligible", "needs_verification", "eligible"]
        dec["eligibility_status"] = min(a["eligibility_status"], b["eligibility_status"], key=order.index)
    status, notes = apply_prefs({**base, "eligibility_status": dec["eligibility_status"]}, prefs)
    final = final_score(dec["professional_match"], status)
    compact = lambda e: {k: e[k] for k in ("professional_match", "eligibility_match", "eligibility_status", "verdict", "why_he")}
    return {"fit": final, "pro": int(dec["professional_match"]), "elig": int(dec["eligibility_match"]), "elig_status": status, "elig_notes": notes,
            "emp": base["employment_type"]["value"], "arr": base["work_arrangement"]["value"], "region": base["location"]["job_region"],
            "langs_req": base.get("languages_required") or [],
            "loc_terms": {k: base["location"][k] for k in ("allowed_work_from", "residency_or_permit")},
            "reqs": base["requirements"][:12], "verdict": dec["verdict"], "why": dec["why_he"], "gaps": dec["gaps"][:4], "lang": lang,
            "cv": base["cv_file"] if base["cv_file"] in pool else (pool[0] if pool else ""),
            "evals": {"a": compact(a), "b": compact(b) if b is not a else None, "tie": tie}, "flag": bool(tie), "usd": usd}


def runs_today(s):
    today = dt.datetime.now(IL).date().isoformat()
    return sum(1 for r in s.get("runs", []) if dt.datetime.fromisoformat(r["at"].replace("Z", "+00:00")).astimezone(IL).date().isoformat() == today)


def archived(s, j):
    if key_of(j) in s["archive"]:
        return True
    urls = set((j.get("links") or {}).values()) | {j.get("url")}
    return any(urls & set(a.get("urls") or []) for a in s["archive"].values())


SOURCES = {"linkedin": "LinkedIn", "alljobs": "AllJobs", "jobmaster": "JobMaster", "drushim": "דרושים", "careers": "דפי קריירה",
           # 21 Nov (Assaf): the only extra remote / EEA source kept in the stack is Himalayas (free); career pages already cover Europe
           "himalayas": "Himalayas"}
EEA_SOURCES = {"himalayas"}
IL_SOURCES = {"alljobs", "jobmaster", "drushim"}
EMP_ARG = {"Full-Time": "full", "Part-Time": "part", "Contract": "contract", "Freelance": "freelance"}
ARR_ARG = {"Remote": "remote", "Hybrid": "hybrid", "On-site": "onsite"}
DEFAULT_PREFS = {"regions": ["IL"], "employment": [], "arrangement": [], "sources": list(SOURCES), "countries": [], "eu_open": True}


def _open_eu(prefs):
    """Assaf 22 Nov: with EEA chosen, Europe is open to him (Polish citizen), not only remote-from-Israel jobs."""
    return bool(prefs and "EEA" in (prefs.get("regions") or []) and prefs.get("eu_open", True))


def prefs_of(s):
    """Assaf 21 Nov: search preferences saved with the data (regions, employment type, work arrangement, sources)."""
    p = {**DEFAULT_PREFS, **(settings_of(s).get("search_prefs") or {})}
    old_all = {"linkedin", "alljobs", "jobmaster", "drushim", "careers"}
    if set(p["sources"]) >= old_all:  # saved before the free remote boards existed: "all" still means all
        p["sources"] = list(dict.fromkeys(list(p["sources"]) + sorted(EEA_SOURCES)))
    return p


def english_term(kw):
    """Assaf 22 Nov: LinkedIn is always searched in English. A Hebrew keyword that is not in the dictionary gets a short English job-search term
    from the cheapest model (cached per keyword). Returns "" when the keyword is already English or known."""
    if not re.search(r"[֐-׿]", kw) or (DICT.get(kw.strip().lower()) or {}).get("en"):
        return ""
    cache = SH.setdefault("en_terms", {})
    if kw not in cache:
        try:
            import anthropic
            r = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY")).messages.create(
                model=MODEL, max_tokens=4000, messages=[{"role": "user", "content": f"Translate this Hebrew job-search keyword into a short English job title or search phrase for LinkedIn. Return only the phrase.\n{kw}"}])
            track(r)
            cache[kw] = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip().strip('"')[:60]
        except Exception:
            cache[kw] = ""
    return cache[kw]


def engine_args(prefs):
    a = ["--regions", ",".join(prefs.get("regions") or ["IL"]), "--boards", ",".join([x for x in (prefs.get("sources") or list(SOURCES)) if x in SOURCES] or list(SOURCES))]
    if prefs.get("arrangement"):
        a += ["--wt", ",".join(ARR_ARG[x] for x in prefs["arrangement"] if x in ARR_ARG)]
    if prefs.get("employment"):
        a += ["--jt", ",".join(EMP_ARG[x] for x in prefs["employment"] if x in EMP_ARG)]
    return a


def search_core(kw, hours, batch, report, prefs=None):
    """One keyword, no Streamlit calls (runs in a background thread). report(phase, **extra) updates the live view."""
    days = hours // 24 if hours in (24, 48, 72) else 7
    report("pulling")
    out = tempfile.mkdtemp()
    extra_path = Path(out, "ats-extra-in.json")
    try:
        extra_path.write_text(json.dumps(ats_extra_load()[0]), encoding="utf-8")
    except Exception:
        extra_path.write_text("[]", encoding="utf-8")
    env = {**os.environ, "APIFY_API_TOKEN": secret("APIFY_API_TOKEN", ""), "JOBENGINE_SERVER": "1", "JOBENGINE_RUNS_DIR": tempfile.gettempdir(),
           "JOBSCOUT_ATS_EXTRA": str(extra_path)}
    en_term = english_term(kw)
    test_free = (["--free"] if os.environ.get("JOBSCOUT_TEST_FREE") == "1" else []) + (["--en", en_term] if en_term else [])  # local test only: free sources, no Apify
    prefs = prefs or DEFAULT_PREFS
    p = subprocess.run(["node", str(APP / "engine/mini-agent/scout.mjs"), kw, "--out", out, "--days", str(days), *engine_args(prefs), *test_free],
                       cwd=str(APP / "engine"), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if p.returncode != 0:
        report("error", error=(p.stdout + p.stderr)[-600:])
        return
    found = json.loads(Path(out, "results-full.json").read_text(encoding="utf-8"))
    res_meta = json.loads(Path(out, "results.json").read_text(encoding="utf-8"))
    stats = res_meta.get("stats", {})  # per source: pulled / dropped / kept
    src_errors = {}  # a failed source never stops the others (engine catches per source); remember what failed
    for r in res_meta.get("runLog", []):
        if r.get("error") or r.get("status") in ("FAILED", "ABORTED", "TIMED-OUT"):
            src_errors[r.get("board", "?")] = str(r.get("error") or r.get("status"))[:120]
    report("sources", sources={b: {"pulled": v.get("pulled", 0), "kept": v.get("kept", 0)} for b, v in stats.items()}, source_errors=src_errors)
    try:  # careers boards found during this search → private repo, used from the next search on
        ats_extra_add(json.loads(Path(out, "ats-new.json").read_text(encoding="utf-8")))
    except Exception:
        pass
    spent = re.search(r'"spentUsd":([\d.]+)', p.stdout)
    s, _ = load_state()
    fresh, seen_again, keys = [], {}, []
    for j in found:
        if archived(s, j):
            continue
        k = key_of(j)
        keys.append(k)
        (seen_again.__setitem__(k, j) if k in s["jobs"] else fresh.append((k, j)))
    blocked = [(k, j, excluded(s, j)) for k, j in fresh if excluded(s, j)]  # user's exclude words / blocked companies: no scoring cost
    fresh = [(k, j) for k, j in fresh if not excluded(s, j)]
    # Assaf 22 Nov: EU country filter - jobs that name only EU countries outside the chosen ones are dropped before scoring (no Claude cost)
    pick = set(prefs.get("countries") or [])
    if pick:
        def _wrong_country(j):
            cs = [c for c in job_countries(j) if c in EU_COUNTRIES]
            return bool(cs) and not (set(cs) & pick) and LOC_IL not in job_countries(j)
        blocked += [(k, j, "מדינה שלא נבחרה: " + ", ".join(c for c in job_countries(j) if c in EU_COUNTRIES)) for k, j in fresh if _wrong_country(j)]
        fresh = [(k, j) for k, j in fresh if not _wrong_country(j)]
    # Assaf 21 Nov: jobs outside Israel must be in English - checked for free before scoring
    blocked += [(k, j, "משרה בחו״ל שלא כתובה באנגלית") for k, j in fresh if is_international(j) and not is_english_post(j)]
    fresh = [(k, j) for k, j in fresh if not (is_international(j) and not is_english_post(j))]
    # Assaf 25 Nov (cost): keywords of one search run in parallel and often return the same posting. The first keyword to reach a job
    # scores it; the others only add their keyword to it (no second Claude bill for the same job).
    with SH["lock"]:
        claims = SH.setdefault("score_claims", {})
        now_t = time.time()
        for ck in [x for x, (_, t) in claims.items() if now_t - t > 3 * 3600]:
            claims.pop(ck, None)
        dup = {k: j for k, j in fresh if k in claims and claims[k][0] != kw}
        for k, j in fresh:
            if k not in dup:
                claims[k] = (kw, now_t)
    fresh = [(k, j) for k, j in fresh if k not in dup]
    report("scoring", found=len(found), new=len(fresh))
    scores, usd = {}, 0.0
    with ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(score_job, j, kw, prefs): k for k, j in fresh}
        for i, f in enumerate(as_completed(futs), 1):
            k = futs[f]
            try:
                sc = f.result()
                usd += sc.pop("usd")
                scores[k] = sc
            except Exception as e:
                scores[k] = {"scoreError": _redact(e)[:160]}
            report("scoring", scored=i)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=HISTORY_HOURS)).isoformat()

    autoc = {}

    intl_out = []

    def mutate(st_):
        intl_out.clear()
        for k, j in list(seen_again.items()) + list(dup.items()):  # dup: scored by another keyword of this search
            h = st_["jobs"].get(k)
            if not h:
                continue
            h["kws"] = sorted(set(h.get("kws", [])) | {kw})
            h["boards"] = sorted(set(h.get("boards", [])) | set(j.get("boards", [])))
            h["links"] = {**(h.get("links") or {}), **(j.get("links") or {})}
        for k, j in fresh:
            st_["jobs"][k] = {"key": k, "title": j["title"], "company": j.get("company"), "location": j.get("location"),
                              "posted": j.get("posted"), "dayOnly": bool(j.get("dayOnly")), "url": j.get("url"), "links": j.get("links"),
                              "boards": j.get("boards", []), "easyApply": bool(j.get("easyApply")), "kws": [kw], "foundAt": now, **scores.get(k, {})}
            if j.get("poster"):
                st_["jobs"][k]["poster"] = str(j["poster"])[:80]  # 21 Nov: LinkedIn job poster, used by direct outreach
            fit = scores.get(k, {}).get("fit")
            if (fit is None or fit >= MIN_FIT) and j.get("desc"):  # kept while the job is open, for "Ask Claude"
                st_["jobs"][k]["desc"] = str(j["desc"])[:3000]
            if is_gtm(j):
                st_["jobs"][k]["gtm"] = True
            out = intl_reason(j, scores.get(k), _open_eu(prefs)) if "fit" in scores.get(k, {}) else None  # outside Israel: English, fully remote, EN/HE only
            if out:
                st_["archive"][k] = {"status": "auto", "reason": "intl", "note": out, "fit": fit, "at": now, "title": j["title"], "company": j.get("company"),
                                     "urls": list((j.get("links") or {"x": j.get("url")}).values())}
                intl_out.append(k)
            elif fit is not None and fit < MIN_FIT:  # Assaf 19 Nov rule of thumb: under MIN_FIT is never shown, whatever else (GTM included)
                st_["archive"][k] = {"status": "auto", "fit": fit, "at": now, "title": j["title"], "company": j.get("company"),
                                     "urls": list((j.get("links") or {"x": j.get("url")}).values())}
        for k, j, why in blocked:
            st_["jobs"][k] = {"key": k, "title": j["title"], "company": j.get("company"), "url": j.get("url"), "boards": j.get("boards", []),
                              "kws": [kw], "foundAt": now, "posted": j.get("posted")}
            st_["archive"][k] = {"status": "auto", "reason": "cyber" if why == "סייבר" else "intl" if why.startswith("משרה בחו") else "excluded", "note": why, "at": now, "title": j["title"], "company": j.get("company"),
                                 "urls": list((j.get("links") or {"x": j.get("url")}).values())}
        auto = len(blocked) + len(intl_out) + sum(1 for k, j in fresh if k not in intl_out and (scores.get(k, {}).get("fit") is not None and scores[k]["fit"] < MIN_FIT))
        autoc["n"] = auto
        runs =[{"kw": kw, "hours": hours or None, "at": now, "batch": batch, "found": len(found), "new": len(fresh), "autoRemoved": auto,
                 "stats": stats, "sourceErrors": src_errors, "prefs": prefs, "keys": keys, "apifyUsd": float(spent.group(1)) if spent else None, "claudeUsd": round(usd, 3)}] + st_.get("runs", [])[:499]
        for r in runs:  # history keeps the job list for 72 hours only
            if r.get("keys") and r["at"] < cutoff:
                r.pop("keys")
        st_["runs"] = runs

    update_state(mutate, f"run {kw} {hours or 'week'}h: {len(fresh)} new")
    report("done", new=len(fresh), auto=autoc.get("n", 0), usd=round(usd, 3))


def start_batch(kws, hours, prefs=None):
    """Start a multi-keyword search in the background (3 keywords at a time) and return its id."""
    batch = uuid.uuid4().hex[:10]
    info = {"kws": list(kws), "hours": hours, "prefs": prefs, "started": time.time(), "kw": {k: {"phase": "queued"} for k in kws}, "done": False}
    SH["batches"][batch] = info

    def run_one(k):
        def report(phase, **extra):
            info["kw"][k] = {**info["kw"][k], "phase": phase, **extra}
        try:
            search_core(k, hours, batch, report, prefs)
        except Exception as e:
            report("error", error=_redact(e)[:300])

    def run_all():
        with ThreadPoolExecutor(3) as ex:
            list(ex.map(run_one, kws))
        info["done"] = True
        info["ended"] = time.time()

    threading.Thread(target=run_all, daemon=True).start()
    return batch


# ---------------- screen (Assaf 19 Nov: app layout — sidebar navigation, inbox / search / history / archive / stats) ----------------
GROUPS = {
    "מוצר": ["מנהל מוצר", "product ai", "ai product owner", "technical product manager", "data product manager", "product operations", "product marketing"],
    "אוטומציה ושיווק": ["מנהל מוצר אוטומציות", "marketing automation", "אוטומציות שיווק", "מנהל פרויקט אוטומציות", "automation", "lifecycle marketing", "מנהל מערכות שיווק"],
    "AI": ["ai builder", "ai automation", "workflow ai", "מיישם פתרונות ai", "ai implementation", "ai solutions"],
    "CRM": ["crm product", "crm integration", "crm automation", "hubspot"],
    "פרסום וקמפיינים": ["campaign manager", "media buyer"],
    "הצלחת לקוח": ["customer success manager"],
    "עבודה זמנית מהבית": ["נציג שירות מהבית", "עבודה מהבית", "remote customer support", "נציג שימור"],
    "הכנסה מהירה (פרילנס / AI)": ["ai trainer", "n8n freelance", "crm setup freelance"],
    "מכירות ו-Pre-sales": ["sales ai solution", "ai sales", "ai solutions sales", "מנהל מכירות פתרונות ai", "account executive ai", "business development ai", "solutions consultant ai"],
    "GTM ועוד": ["gtm product", "gtm ai", "gtm engineer", "gtm automation", "טרנספורמציה דיגיטלית", "מנהל חדשנות"],
}
# Assaf 19 Nov (research report): run these, twice a day if possible - the keywords that lead to applications,
# plus small keywords with a high send rate. Noisy ones (AI Automation, AI Solutions, GTM AI) stay out.
RECOMMENDED = ["מנהל מוצר אוטומציות", "מנהל מוצר", "product ai", "technical product manager", "ai builder", "marketing automation",
               "gtm engineer", "מנהל חדשנות", "מיישם פתרונות ai", "ai implementation", "ai product owner"]
TOP10 = ["מנהל מוצר אוטומציות", "marketing automation", "ai builder", "אוטומציות שיווק", "gtm product", "product ai", "מנהל מוצר",
         "gtm engineer", "product marketing", "ai automation"]
NEW12 = ["מיישם פתרונות ai", "ai implementation", "ai solutions", "ai product owner", "technical product manager", "data product manager",
         "product operations", "crm product", "lifecycle marketing", "טרנספורמציה דיגיטלית", "מנהל חדשנות", "מנהל מערכות שיווק"]
USD_PER_KW = 0.25  # measured 18 Nov: Apify ~$0.16 + Claude ~$0.09 per keyword
AGE = {"all": "הכל", "1": "יום", "3": "3 ימים", "7": "שבוע", "older": "מעל שבוע"}
PHASE = {"queued": "ממתין", "pulling": "מושך משרות", "sources": "המקורות סיימו", "scoring": "מדרג", "done": "הסתיים", "error": "נכשל"}


def lbl(k):
    return LABEL.get(k, k)


def to_il(iso):
    return dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(IL)


def ago(j):
    p = j.get("posted")
    if not p:
        return "?"
    t = dt.datetime.fromisoformat(p.replace("Z", "+00:00")) if "T" in p else dt.datetime.fromisoformat(p + "T21:59:59+00:00")
    if j.get("dayOnly"):
        return t.astimezone(IL).strftime("%d.%m")
    h = max(0, round((dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600))
    return "עכשיו" if h < 1 else (f"לפני {h} ש'" if h < 24 else f"לפני {round(h / 24)} ימים")


def age_h(j):
    p = j.get("posted")
    if not p:
        return 1e9
    t = dt.datetime.fromisoformat(p.replace("Z", "+00:00")) if "T" in p else dt.datetime.fromisoformat(p + "T21:59:59+00:00")
    return (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600


def mark_many(keys, status, block=False):
    """Sent / removed for one or many jobs in ONE save; remembers the last action so it can be undone.
    Assaf 19 Nov: block=True also blocks the jobs' companies (status None = block only, the jobs stay open but hidden)."""
    keys = list(keys)
    newly, noname, prev = [], [], {}

    def m(s):
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        prev.clear()
        cfg = settings_of(s)
        newly.clear()
        noname.clear()
        for key in keys:
            j = s["jobs"].get(key, {})
            if block:
                ck = company_key(j.get("company"))
                if not ck:
                    noname.append(key)
                elif ck not in cfg["blocked_companies"]:
                    cfg["blocked_companies"].append(ck)
                    newly.append(ck)
            if status:
                prev[key] = s["archive"].get(key)
                if j.get("desc"):
                    j["desc"] = str(j["desc"])[:3000]  # 26 Nov: trimmed, no longer deleted (drafts after "sent" had no job text)
                s["archive"][key] = {"status": status, "at": now, "title": j.get("title"), "company": j.get("company"),
                                     "urls": list((j.get("links") or {"x": j.get("url")}).values())}
    what = (status or "") + ("+block" if block else "")
    update_state(m, f"{what}: {keys[0]}" if len(keys) == 1 else f"{what} x{len(keys)}")
    st.session_state.undo = {"keys": keys if status else [], "status": status, "blocked": list(newly), "noname": len(noname), "prev": dict(prev)}
    for k in keys:
        st.session_state.pop(f"sel-{k}", None)


def restore_many(keys, unblock=(), prev=None):
    """prev = the archive entries before the action being undone; a job that was already archived goes back to that state."""
    keys, unblock, prev = list(keys), list(unblock), prev or {}

    def m(s):
        for key in keys:
            if prev.get(key):
                s["archive"][key] = prev[key]
            else:
                s["archive"].pop(key, None)
        if unblock:
            cfg = settings_of(s)
            cfg["blocked_companies"] = [c for c in cfg["blocked_companies"] if c not in unblock]
    update_state(m, f"restore x{len(keys)}" + (f" unblock x{len(unblock)}" if unblock else ""))


def force_back(keys):
    """Assaf 21 Nov: bring jobs under the score threshold back to the inbox on purpose (forced = skip the MIN_FIT hide)."""
    keys = list(keys)

    def m(s):
        for key in keys:
            s["archive"].pop(key, None)
            if key in s["jobs"]:
                s["jobs"][key]["forced"] = True
    update_state(m, f"force back x{len(keys)}")


def mark(key, status):  # kept for older callers
    if status == "restore":
        restore_many([key])
    else:
        mark_many([key], status)


def sent_today(s):
    today = dt.datetime.now(IL).date()
    return sum(1 for a in s["archive"].values() if a.get("status") == "sent" and a.get("at") and to_il(a["at"]).date() == today)


@st.cache_data(ttl=300, show_spinner=False)
def apify_usage():
    try:
        d = requests.get("https://api.apify.com/v2/users/me/limits", headers={"Authorization": "Bearer " + str(secret("APIFY_API_TOKEN"))}, timeout=10).json()["data"]
        return d["current"]["monthlyUsageUsd"], d["limits"]["maxMonthlyUsageUsd"]
    except Exception:
        return None, None


def active_batch():
    """The search running now on this server (it survives a page refresh), else this session's last one."""
    live = [(b, i) for b, i in SH["batches"].items() if not i["done"]]
    if live:
        return max(live, key=lambda x: x[1]["started"])
    b = st.session_state.get("batch")
    return (b, SH["batches"][b]) if b in SH["batches"] else (None, None)


def batch_view(info, compact=False):
    done = sum(1 for v in info["kw"].values() if v.get("phase") in ("done", "error"))
    total = len(info["kws"])
    secs = int((info.get("ended") or time.time()) - info["started"])
    new = sum(v.get("new", 0) or 0 for v in info["kw"].values() if v.get("phase") == "done")
    head = f"{'החיפוש הסתיים' if info['done'] else 'מחפש…'} · {done}/{total} מילים · {new} חדשות · {secs // 60}:{secs % 60:02d} דק'"
    st.progress(done / max(1, total), text=head)
    if compact:
        return
    rows = []
    for k in info["kws"]:
        v = info["kw"][k]
        extra = ""
        if v.get("phase") == "scoring" and v.get("new") is not None:
            extra = f"{v.get('scored', 0)}/{v['new']}"
        elif v.get("phase") == "done":
            # Assaf 24 Nov: plain wording. "auto" = filtered automatically (score under MIN_FIT, abroad, cyber, blocked words)
            _n, _a = v.get("new", 0) or 0, v.get("auto") or 0
            extra = f"{_n} משרות חדשות" + (f" · {max(_n - _a, 0)} הוצגו · {_a} סוננו (ציון מתחת ל-{MIN_FIT} / חו״ל / סייבר)" if _a else " · כולן הוצגו" if _n else "")
        elif v.get("phase") == "error":
            extra = str(v.get("error", ""))[:80]
        rows.append({"": {"done": "✅", "error": "⚠️"}.get(v.get("phase"), "⏳"), "מילה": lbl(k), "שלב": PHASE.get(v.get("phase"), v.get("phase")), "פרטים": extra})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    # Assaf 21 Nov: status per source, summed over the keywords that finished pulling
    src = {}
    for v in info["kw"].values():
        for b, c in (v.get("sources") or {}).items():
            o = src.setdefault(b, {"pulled": 0, "kept": 0, "errors": 0})
            o["pulled"] += c.get("pulled", 0)
            o["kept"] += c.get("kept", 0)
        for b in (v.get("source_errors") or {}):
            src.setdefault(b, {"pulled": 0, "kept": 0, "errors": 0})["errors"] += 1
    if src:
        st.caption("לפי מקור (כל המילים שסיימו)")
        st.dataframe(pd.DataFrame([{"מקור": BOARD.get(b, b), "סטטוס": "⚠️ נכשל ב-" + str(o["errors"]) + " מילים" if o["errors"] else "✅ תקין",
                                    "נשלפו": o["pulled"], "נכנסו אחרי סינון": o["kept"]} for b, o in sorted(src.items(), key=lambda x: -x[1]["kept"])]),
                     hide_index=True, width="stretch")


def progress_live(compact=False):
    b, info = active_batch()
    if not info:
        return
    batch_view(info, compact)
    if info["done"] and st.session_state.get("seen_batch") != b:
        st.session_state.seen_batch = b
        st.rerun(scope="app")  # refresh lists and counters once the search ends


def undo_bar():
    u = st.session_state.get("undo")
    if not u:
        return
    parts = []
    if u.get("status"):
        parts.append(f"{len(u['keys'])} משרות " + ("סומנו כנשלחו" if u["status"] == "sent" else "הוסרו"))
    if u.get("blocked"):
        parts.append(f"{len(u['blocked'])} חברות נחסמו")
    if u.get("noname"):
        parts.append(f"{u['noname']} משרות חסויות, בלי שם חברה, לא נחסמו")
    if not parts:
        parts.append("החברה כבר הייתה חסומה")
    a, b, c = st.columns([5, 1, 1], vertical_alignment="center")
    a.success(" · ".join(parts))
    if b.button("↩ בטל", key="undo_btn", width="stretch"):
        restore_many(u["keys"], u.get("blocked") or [], u.get("prev"))
        st.session_state.pop("undo", None)
        st.rerun()
    if c.button("סגור", key="undo_close", width="stretch"):
        st.session_state.pop("undo", None)
        st.rerun()


def new_since(s):
    runs_ = s.get("runs", [])
    if not runs_:
        return "9999"
    last_b = max(runs_, key=lambda r: r["at"]).get("batch")
    return min((r["at"] for r in runs_ if last_b and r.get("batch") == last_b), default="9999")


ASK_PRESETS = ["מה הדרישות העיקריות במשרה?", "איך להתאים את קורות החיים למשרה הזאת?",
               "כתוב מכתב פנייה קצר (עד 100 מילים) למשרה", "אילו שאלות כדאי לי להכין לראיון?"]


def ask_claude(j, question, prior):
    """Assaf 19 Nov: free-form question about one job. Opus 5.5 at low effort (~$0.02–0.04 a question)."""
    import anthropic
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
    post = "\n".join([f"TITLE: {j.get('title')}", f"COMPANY: {j.get('company') or 'not stated'}", f"LOCATION: {j.get('location') or 'Israel'}",
                      f"POSTED: {ago(j)}", f"LINK: {j.get('url')}", f"FIT SCORE: {j.get('fit')}", f"WHY IT FITS (earlier review): {j.get('why') or ''}",
                      f"GAPS: {', '.join(map(str, j.get('gaps') or []))}", "", "JOB DESCRIPTION:",
                      j.get("desc") or "(not saved for this job - answer from the title, company and the review above, and say the full post was not available)"])
    system = (BRAIN + "\n\n---\nYou now answer " + PF.first_name(profile()) + "'s questions about ONE job post (below). Answer in simple, natural Hebrew, "
              "speaking to him in male second person; keep tool and product names in English. Be concrete and short (max ~150 words) unless he "
              "asks for a letter or a list. Never invent facts about the company or the post; say when something is not in the post. "
              "Never state how many years of experience he has.\n\nJOB POST:\n" + post)
    msgs = []
    for q, a in prior[-4:]:
        msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    msgs.append({"role": "user", "content": question})
    r = client.beta.messages.create(model=MODEL, max_tokens=16000, system=system, messages=msgs,
                                    output_config={"effort": "low"}, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    track(r)
    if r.stop_reason == "refusal":
        return "Claude לא ענה על השאלה הזאת. נסה לנסח אותה אחרת."
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip() or "(אין תשובה)"


def ask_panel(j):
    k = j["key"]
    hist = st.session_state.setdefault("ask_hist", {}).setdefault(k, [])
    with st.container(border=True):
        st.markdown("<span class='ai-h'>✨ שאל את Claude על המשרה הזאת</span>"
                    + ("" if j.get("desc") else "<br><span class='sub'>תיאור המשרה לא נשמר עבור משרה זו, Claude יענה לפי הכותרת וההערכה</span>"),
                    unsafe_allow_html=True)
        for q, a in hist:
            st.markdown(f"**שאלת:** {q}")
            with st.container(border=True):
                st.markdown(a)
        cols = st.columns(len(ASK_PRESETS))
        asked = None
        for i, q in enumerate(ASK_PRESETS):
            if cols[i].button(q, key=f"askp-{k}-{i}", width="stretch"):
                asked = q
        with st.form(f"askf-{k}", clear_on_submit=True, border=False):
            a1, a2 = st.columns([5, 1], vertical_alignment="bottom")
            free = a1.text_input("שאלה משלך", placeholder="שאלה משלך…", label_visibility="collapsed")
            if a2.form_submit_button("שאל", type="primary", width="stretch") and free.strip():
                asked = free.strip()
        if asked:
            with st.spinner("Claude חושב…"):
                try:
                    hist.append((asked, ask_claude(j, asked, hist)))
                except Exception as e:
                    st.info(f"השאלה נכשלה: {_redact(e)[:200]}", icon="⚠️")
                    return
            st.rerun()


CTX = {"sent_by_co": {}, "followed": set()}


def fit_cls(fit):
    return "f-na" if fit is None else "f-hi" if fit >= 80 else "f-mid" if fit >= 60 else "f-lo"


def card(j, new=False):
    k = j["key"]
    fit = j.get("fit")
    cv = j.get("cv") or ""
    tags = '<span class="tag t-new">חדשה</span>' if new else ""
    if is_gtm(j):
        tags += '<span class="tag t-ok">GTM</span>'
    ck = company_key(j.get("company"))
    if ck and ck in CTX["followed"]:
        tags += '<span class="tag t-star">⭐ במעקב</span>'
    tags += "".join(f'<span class="tag t-kw">{lbl(x)}</span>' for x in j.get("kws", []))
    tags += "".join(f'<span class="tag">{BOARD.get(b, b)}</span>' for b in j.get("boards", []))
    if j.get("easyApply"):
        tags += '<span class="tag t-ok">Easy Apply</span>'
    if cv:
        tags += f'<span class="tag t-cv">קו״ח {"עב" if j.get("lang") == "he" else "EN"}: {E(cv.replace(".pdf", ""))}</span>'
    # Assaf 21 Nov: verified scoring - terms + eligibility shown on the card, both reviews in the details
    if j.get("elig_status"):
        cls = {"eligible": "t-ok", "needs_verification": "t-warn", "not_eligible": "t-no"}.get(j["elig_status"], "")
        tags += f'<span class="tag {cls}">{ELIG_HE.get(j["elig_status"], j["elig_status"])}</span>'
        tags += f'<span class="tag">{EMP_HE.get(j.get("emp"), j.get("emp"))} · {ARR_HE.get(j.get("arr"), j.get("arr"))}</span>'
        if j.get("flag"):
            tags += '<span class="tag t-kw">⚖️ הוכרע</span>'
    why = E(str(j.get("why") or (f"הדירוג נכשל: {j['scoreError']}" if j.get("scoreError") else "")))
    gaps = j.get("gaps") or []
    more = ("<br>" + " · ".join(E(str(g)) for g in gaps[:3])) if gaps else ""
    if j.get("pro") is not None:
        ev = j.get("evals") or {}
        icon = {"met": "✅", "partial": "🟡", "missing": "❌", "unknown": "❔"}
        reqs = "".join(f"<li>{icon.get(q.get('status'), '')} {E(str(q.get('requirement') or ''))} {'(חובה)' if q.get('kind') == 'must' else ''}</li>"
                       for q in (j.get("reqs") or [])[:8])
        lt = j.get("loc_terms") or {}
        more += (f"<br><b>מקצועי {j['pro']} · זכאות {j.get('elig')} · סופי {fit}</b>"
                 + (" · " + " · ".join(E(str(x)) for x in (j.get("elig_notes") or [])) if j.get("elig_notes") else "")
                 + f"<br>מותר לעבוד מ: {E(str(lt.get('allowed_work_from', '?')))} · מגבלות: {E(str(lt.get('residency_or_permit', '?')))}"
                 + (f"<ul style='margin:4px 0'>{reqs}</ul>" if reqs else "")
                 + f"<span class='sub'>הערכה א׳ {(ev.get('a') or {}).get('professional_match')} · הערכה ב׳ {(ev.get('b') or {}).get('professional_match', 'לא נדרשה')}"
                 + (f" · הכרעה: {E(str(ev['tie'].get('disagreement') or ''))}" if ev.get("tie") else "") + "</span>")
    with st.container(border=True):
        c0, c1, c2 = st.columns([0.35, 0.9, 10], vertical_alignment="top")
        c0.checkbox("בחר", key=f"sel-{k}", label_visibility="collapsed")
        c1.markdown(f'<div class="fit {fit_cls(fit)}">{fit if fit is not None else "—"}<small>התאמה</small></div>'
                    + (f'<div class="subfit">מקצועי {j["pro"]}<br>זכאות {j.get("elig")}</div>' if j.get("pro") is not None else ""), unsafe_allow_html=True)
        c2.markdown(
            f'<div class="jt"><a href="{E(str(j.get("url") or "#"))}" target="_blank" rel="noopener">{E(str(j.get("title") or ""))}</a></div>'
            f'<div class="sub">{E(str(j.get("company") or "—"))} · {E(str(j.get("location") or "ישראל"))} · {ago(j)}</div>'
            + (f'<div class="warn">⚠️ כבר שלחת לחברה הזאת {CTX["sent_by_co"][ck]} פעמים</div>' if ck and CTX["sent_by_co"].get(ck) else "")
            + f'<div class="tags">{tags}</div>'
            + (f'<details class="why"><summary>למה זה מתאים</summary>{why}{more}</details>' if why else ""),
            unsafe_allow_html=True)
        a1, a2, a3, a4, a5, a6, a7 = c2.columns([2, 1.2, 1.2, 1.1, 1.6, 0.9, 1.6])
        co = j.get("company") or ""
        if a7.button("👤 מצא איש קשר", key=f"out-{k}", width="stretch", help="מציאת איש קשר, מייל מאומת וטיוטה לאישור"):
            st.session_state.outreach_job = k
            st.switch_page(PAGES["outreach"])
        with a6.popover("🚫", help="חסימת החברה", width="stretch"):
            if not company_key(co):
                st.caption("אי אפשר לחסום משרה בלי שם חברה")
            else:
                st.markdown(f"**{co}**")
                if st.button("🚫 חסום חברה", key=f"blk-{k}", width="stretch", help="המשרות שלה ייעלמו ולא יחזרו בחיפושים"):
                    mark_many([k], None, block=True)
                    st.rerun()
                if st.button("✓ שלחתי + חסום חברה", key=f"sblk-{k}", width="stretch"):
                    mark_many([k], "sent", block=True)
                    st.rerun()
                if st.button("✕ הסר + חסום חברה", key=f"xblk-{k}", width="stretch"):
                    mark_many([k], "removed", block=True)
                    st.rerun()
        a1.link_button("פתח והגש ↗", j.get("url") or "#", type="primary", width="stretch")
        if a2.button("📝 התאם קו״ח", key=f"c-{k}", width="stretch", help="התאמת קורות החיים למשרה הזאת (עמוד התאמת קו״ח)"):
            st.session_state.tailor_job = k  # Assaf 21 Nov: download + tailor live on the "התאמת קו״ח" page
            st.switch_page(PAGES["tailor"])
        if a3.button("✓ שלחתי", key=f"s-{k}", width="stretch"):
            mark_many([k], "sent")
            st.rerun()
        if a4.button("✕ הסר", key=f"x-{k}", width="stretch"):
            mark_many([k], "removed")
            st.rerun()
        open_ask = st.session_state.get("ask_open") == k
        if a5.button("✨ שאל את Claude" if not open_ask else "סגור את Claude", key=f"ask-{k}", width="stretch"):
            st.session_state.ask_open = None if open_ask else k
            st.rerun()
        g1, g2, g3, _g = c2.columns([1.6, 1.6, 1.8, 2.4])
        bd = set(j.get("boards") or [])
        if bd & {"careers", "himalayas"} and "linkedin" not in bd:  # Assaf 21 Nov: same job on LinkedIn (poster, Easy Apply, people)
            g3.link_button("🔗 מצא בלינקדאין", "https://www.linkedin.com/jobs/search/?keywords=" + requests.utils.quote(f"{j.get('title') or ''} {j.get('company') or ''}".strip()),
                           width="stretch", help="חיפוש המשרה הזאת בלינקדאין לפי הכותרת והחברה")
        for col, kind, label in ((g1, "cover", "✍️ מכתב מקדים"), (g2, "note", "💬 הערה אישית")):
            with col.popover(label, width="stretch"):
                _gen_panel(j, kind)
    if st.session_state.get("ask_open") == k:
        ask_panel(j)


GEN_RULES = ("Rules: write as {PERSONA}, human and plain, "
             "no invented numbers or claims, never mention what he lacks or has not done, no flattery, no links, do not mention a CV or attachment. "
             "Use only what the job text supports.")


def _gen_text(j, kind):
    import anthropic
    he = j.get("lang") == "he"
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"), max_retries=3)
    if kind == "cover":
        ask = (f"Write a cover letter of about 100 words in {'Hebrew' if he else 'English'} for the role below. Open with why this role, then one or two concrete "
               "matches between his experience and the job, then a short close. ")
    else:
        ask = ("Write a short personal note (max 280 characters) in " + ("Hebrew" if he else "English") + " to send to the person who posted this role "
               "(LinkedIn connection note or a first message). Say he saw the role and one concrete reason it fits. ")
    r = client.messages.create(model=MODEL, max_tokens=8000, messages=[{"role": "user", "content": (
        f"Role: {j.get('title')} at {j.get('company')} ({j.get('location')})\nJob text: {(j.get('desc') or j.get('why') or '')[:2200]}\n\n" + ask + GEN_RULES.replace("{PERSONA}", PF.persona(profile())) +
        " Return only the text.")}])
    track(r)
    t = "".join(b.text for b in r.content if b.type == "text").strip()
    if not t:  # the model spent the whole budget before writing: say so, never show an empty box
        raise RuntimeError(f"Claude לא החזיר טקסט ({r.stop_reason}). נסה שוב")
    return t[:290] if kind == "note" else t


def _cover_pdf(text, rtl):
    """One-page letter as PDF (DejaVu Sans, Hebrew shaped and right-to-left when the job is Hebrew)."""
    from fpdf import FPDF
    pdf = FPDF(format="A4")
    pdf.set_margins(22, 22, 22)
    pdf.add_page()
    pdf.add_font("DV", "", str(APP / "fonts" / "DejaVuSans.ttf"))
    pdf.set_font("DV", size=11)
    pdf.set_text_shaping(True)
    for para in [x for x in text.split("\n\n") if x.strip()] or [text]:
        pdf.multi_cell(0, 6.2, para.strip(), align="R" if rtl else "L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    return bytes(pdf.output())


def _save_cover(fn, text, rtl):
    """Copy of the PDF into cover-letter/ in the private data repo (a local script syncs it to the Cover Letter folder on the PC)."""
    try:
        data = base64.b64encode(_cover_pdf(text, rtl)).decode()
        url = gh_url("cover-letter/" + fn)
        old = requests.get(url, headers=gh_headers(), timeout=20)
        body = {"message": "cover letter " + fn, "content": data}
        if old.status_code == 200:
            body["sha"] = old.json()["sha"]
        requests.put(url, headers=gh_headers(), json=body, timeout=40)
    except Exception:
        pass


def _save_resume(fn, md, lang):
    """Copy of the tailored CV PDF into resume/<en|he>/ in the private data repo; the local sync script puts it in the Resume English or Hebrew folder."""
    try:
        from cv_pdf import cv_pdf
        url = gh_url(f"resume/{lang}/{fn}")
        old = requests.get(url, headers=gh_headers(), timeout=20)
        body = {"message": "tailored cv " + fn, "content": base64.b64encode(cv_pdf(md, profile())).decode()}
        if old.status_code == 200:
            body["sha"] = old.json()["sha"]
        requests.put(url, headers=gh_headers(), json=body, timeout=40)
    except Exception:
        pass


def _cover_docx(text, rtl):
    """Plain Word letter: the edited text, paragraphs kept, right-to-left for Hebrew."""
    import io
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    d = Document()
    d.styles["Normal"].font.name = "Calibri"
    d.styles["Normal"].font.size = Pt(11)
    for para in [x for x in text.split("\n\n") if x.strip()] or [text]:
        p = d.add_paragraph(para.strip())
        p.paragraph_format.space_after = Pt(10)
        if rtl:
            pPr = p._p.get_or_add_pPr()
            pPr.append(OxmlElement("w:bidi"))
            p.alignment = 2
            for r in p.runs:
                rPr = r._r.get_or_add_rPr()
                rPr.append(OxmlElement("w:rtl"))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _gen_panel(j, kind):
    k = j["key"]
    G = st.session_state.setdefault("gen", {})
    gk = f"{k}|{kind}"
    title = "מכתב מקדים (כ-100 מילים)" if kind == "cover" else "הערה אישית (עד 300 תווים)"
    st.markdown(f"**{title}**")
    if not j.get("desc"):
        st.caption("תיאור המשרה המלא לא נשמר. הטקסט יתבסס על הכותרת ועל הסיבות לציון.")
    if st.button("צור מחדש" if G.get(gk) else "צור", key=f"gb-{gk}", type="primary", width="stretch"):
        with st.spinner("Claude כותב…"):
            try:
                G[gk] = _gen_text(j, kind)
                st.session_state[f"gt-{gk}"] = G[gk]  # write the box's state directly: a keyed box ignores a new value=
            except Exception as e:
                st.warning(f"הכתיבה נכשלה: {_redact(e)[:150]}")
    if G.get(gk):
        if not str(st.session_state.get(f"gt-{gk}") or "").strip():  # Streamlit drops widget state while the popover is closed
            st.session_state[f"gt-{gk}"] = G[gk]
        st.text_area("טקסט", height=200 if kind == "cover" else 100, key=f"gt-{gk}", label_visibility="collapsed")
        txt = st.session_state.get(f"gt-{gk}") or G[gk]
        st.caption("ערוך כאן, ואז סמן והעתק. הטקסט לא נשלח לשום מקום.")
        if kind == "cover":
            fn = "Cover-Letter-" + re.sub(r"[^A-Za-z0-9]+", "-", str(j.get("company") or "job")).strip("-") + ".pdf"
            st.download_button("⬇ הורד כ-PDF", _cover_pdf(txt, j.get("lang") == "he"), file_name=fn, key=f"gd-{gk}", width="stretch",
                               mime="application/pdf", on_click=_save_cover, args=(fn, txt, j.get("lang") == "he"))
            st.caption("בלחיצה הקובץ יורד, ועותק נשמר גם בתיקיית cover-letter במאגר הנתונים. סנכרון לתיקיית Cover Letter במחשב: Sync-Cover-Letters.bat.")


def block_actions(sel, where):
    """Assaf 19 Nov: block the companies of the selected jobs, alone or together with sent / remove."""
    c1, c2, c3, _ = st.columns([2, 2, 2, 3], vertical_alignment="center")
    if c1.button(f"✓ נשלחו + 🚫 חסום ({len(sel)})", key=f"sblk_{where}", disabled=not sel, width="stretch"):
        mark_many(sel, "sent", block=True)
        st.rerun()
    if c2.button(f"✕ הסר + 🚫 חסום ({len(sel)})", key=f"xblk_{where}", disabled=not sel, width="stretch"):
        mark_many(sel, "removed", block=True)
        st.rerun()
    if c3.button(f"🚫 חסום חברות ({len(sel)})", key=f"blk_{where}", disabled=not sel, width="stretch",
                 help="המשרות של החברות האלה ייעלמו מהתיבה ויוסרו בחיפושים הבאים"):
        mark_many(sel, None, block=True)
        st.rerun()


def job_table(rows, key, height=None):
    """Selectable table; returns the selected job keys."""
    if not rows:
        return []
    df = pd.DataFrame([{
        "ציון": r.get("fit") if r.get("fit") is not None else 0,
        "תפקיד": r.get("title") or "", "חברה": r.get("company") or "", "פורסם": ago(r),
        "מילה": ", ".join(lbl(x) for x in r.get("kws", [])), "מקור": ", ".join(BOARD.get(b, b) for b in r.get("boards", [])),
        "קישור": r.get("url") or "", "למה": r.get("why") or "", **({"סטטוס": r["_status"]} if "_status" in r else {}),
        **({"תאריך": to_il(r["at"]).strftime("%d.%m %H:%M")} if r.get("at") else {}),
    } for r in rows])
    ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row", key=key,
                      height=height or min(38 + 35 * len(rows), 640),
                      column_config={
                          "ציון": st.column_config.ProgressColumn("ציון", min_value=0, max_value=100, format="%d", width="small"),
                          "תפקיד": st.column_config.TextColumn("תפקיד", width="large"),
                          "קישור": st.column_config.LinkColumn("קישור", display_text="פתח ↗", width="small"),
                          "למה": st.column_config.TextColumn("למה", width="large"),
                      })
    return [rows[i]["key"] for i in ev.selection.rows] if ev and ev.selection else []


# ---------------- pages ----------------
def page_inbox():
    b, info = active_batch()
    running = bool(info and not info["done"])
    st.fragment(run_every=6 if running else None)(inbox_body)()  # new jobs flow in while a search runs


def open_jobs(s):
    """26 Nov: the ONE definition of an open job (inbox, sidebar and stats all count with it).
    Returns (shown, hidden): hidden = open but hidden by the user's own words / blocked companies."""
    queue = s.get("queue", {})
    opn = [j for j in s["jobs"].values() if j["key"] not in s["archive"] and j["key"] not in queue]
    hidden = [j for j in opn if excluded(s, j)]
    eu = _open_eu(prefs_of(s))
    shown = [j for j in opn if not excluded(s, j) and not (j.get("fit") is not None and j["fit"] < MIN_FIT and not j.get("forced"))  # under MIN_FIT: only when forced back
             and not (j.get("arr") and intl_reason(j, None, eu))]  # outside Israel: English, fully remote, EN/HE only
    return shown, hidden


def inbox_body():
    s, _ = load_state()
    undo_bar()
    opn, hidden = open_jobs(s)
    cfg = settings_of(s)
    CTX["followed"] = set(cfg["followed_companies"])
    CTX["sent_by_co"] = {}
    for k, a in s["archive"].items():
        if a.get("status") == "sent":
            ck = company_key(a.get("company") or (s["jobs"].get(k) or {}).get("company"))
            if ck:
                CTX["sent_by_co"][ck] = CTX["sent_by_co"].get(ck, 0) + 1
    ns = new_since(s)
    is_new = lambda j: (j.get("foundAt") or "") >= ns
    hist = st.session_state.get("hist")
    if hist:
        hk = set(hist["keys"])
        opn = [j for j in opn if j["key"] in hk]
        a, c = st.columns([5, 1], vertical_alignment="center")
        a.info(f"מציג חיפוש שמור: {hist['label']}")
        if c.button("הצג הכל", key="hist_back", width="stretch"):
            st.session_state.pop("hist", None)
            st.rerun()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("פתוחות", len(opn))
    m2.metric("חדשות", help="נמצאו בחיפוש האחרון", value= sum(1 for j in opn if is_new(j)))
    m3.metric("ציון 70+", sum(1 for j in opn if (j.get("fit") or 0) >= 70))
    m4.metric("נשלחו היום", sent_today(s))

    with st.container(border=True):
        f1, f2, f4, f5 = st.columns([3, 2.2, 1.8, 2])
        q = f1.text_input("חיפוש", placeholder="🔎 חברה או תפקיד…", key="f_q", label_visibility="collapsed")
        kw_opts = sorted({k for j in opn for k in j.get("kws", [])}, key=lbl)
        f_kw = f2.multiselect("מילה", kw_opts, format_func=lbl, placeholder="כל המילים", key="f_kw", label_visibility="collapsed")
        src_opts = sorted({x for j in opn for x in j.get("boards", [])})
        f_min = f4.selectbox("ציון", [0, 60, 70, 80], format_func=lambda v: "כל הציונים" if not v else f"ציון {v}+", key="f_min", label_visibility="collapsed")
        f_loc = f5.multiselect("מיקום", [LOC_ALL_EU, LOC_IL, LOC_REMOTE] + sorted(EU_COUNTRIES), placeholder="כל המיקומים", key="f_loc",
                               label_visibility="collapsed", help="לפי המדינה שכתובה במיקום המשרה. האיחוד האירופי = כל 27 המדינות")
        # Assaf 25 Nov: sources as always-visible buttons with the number of open jobs in each
        src_n = {x: sum(1 for j in opn if x in j.get("boards", [])) for x in src_opts}
        if "f_src" in st.session_state:  # a source with no open jobs left must not stay selected
            st.session_state.f_src = [x for x in (st.session_state.f_src or []) if x in src_opts]
        f_src = st.pills("מקור (ריק = כל המקורות)", sorted(src_opts, key=lambda x: -src_n[x]), selection_mode="multi", key="f_src",
                         format_func=lambda x: f"{BOARD.get(x, x)} ({src_n[x]})") or []
        g1, g2, g5, g6, g3, g4 = st.columns([3, 1.2, 1.3, 1.5, 1.7, 1.9])
        age = g1.segmented_control("פורסם", list(AGE), format_func=lambda a: AGE[a], default="all", key="age_view", label_visibility="collapsed") or "all"
        only_new = g2.toggle("רק חדשות", key="only_new")
        only_star = g5.toggle("⭐ במעקב", key="only_star", help="רק חברות שסימנת למעקב בעמוד \"חברות\"")
        only_easy = g6.toggle(f"⚡ Easy Apply ({sum(1 for j in opn if j.get('easyApply'))})", key="only_easy", help="רק משרות LinkedIn עם הגשה קלה (Easy Apply)")
        sort = g3.selectbox("מיון", ["fit", "new", "company"], format_func={"fit": "מיון: התאמה", "new": "מיון: הכי חדש", "company": "מיון: חברה"}.get,
                            key="sort", label_visibility="collapsed")
        view = g4.segmented_control("תצוגה", ["cards", "table"], format_func={"cards": "▦ כרטיסים", "table": "☰ טבלה"}.get, default="cards",
                                    key="view", label_visibility="collapsed") or "cards"

    shown = opn
    if q.strip():
        ql = q.strip().lower()
        shown = [j for j in shown if ql in str(j.get("title", "")).lower() or ql in str(j.get("company", "")).lower()]
    if f_kw:
        shown = [j for j in shown if set(f_kw) & set(j.get("kws", []))]
    if f_src:
        shown = [j for j in shown if set(f_src) & set(j.get("boards", []))]
    if f_min:
        shown = [j for j in shown if (j.get("fit") or 0) >= f_min]
    if f_loc:
        shown = [j for j in shown if loc_match(j, f_loc)]
    if only_new:
        shown = [j for j in shown if is_new(j)]
    if only_star:
        shown = [j for j in shown if company_key(j.get("company")) in CTX["followed"]]
    if only_easy:
        shown = [j for j in shown if j.get("easyApply")]
    if hidden:
        st.caption(f"🚫 {len(hidden)} משרות פתוחות מוסתרות לפי המילים והחברות שבעמוד \"סינון\"")
    if age != "all":
        lim = lambda j: 0 if j.get("dayOnly") else 1
        shown = [j for j in shown if ((age_h(j) > 7 * 24 + lim(j)) if age == "older" else (age_h(j) <= int(age) * 24 + lim(j)))]
    shown = sorted(shown, key={"fit": lambda j: (-(j.get("fit") if j.get("fit") is not None else -1), age_h(j)),
                               "new": age_h, "company": lambda j: str(j.get("company") or "")}[sort])

    if not shown:
        if not opn:
            st.info("אין משרות פתוחות 🎉  כל מה שנמצא כבר סומן. לחיפוש חדש עבור ל\"חיפוש חדש\" בתפריט.")
        else:
            st.info("אין משרות שמתאימות לסינון. נקה את הסינון כדי לראות את כל המשרות הפתוחות.")
        return

    if view == "table":
        st.caption(f"{len(shown)} משרות · סמן שורות בעמודה השמאלית ואז בחר פעולה")
        sel = job_table(shown, key="tbl_inbox", height=640)
        t1, t2, _ = st.columns([2, 2, 4])
        if t1.button(f"✓ סמן כנשלחו ({len(sel)})", key="tbl_sent", disabled=not sel, type="primary", width="stretch"):
            mark_many(sel, "sent")
            st.rerun()
        if t2.button(f"✕ הסר ({len(sel)})", key="tbl_rm", disabled=not sel, width="stretch"):
            mark_many(sel, "removed")
            st.rerun()
        block_actions(sel, "table")
        return

    n_all = len(shown)
    p1, p2, p3, p4 = st.columns([1.6, 3, 1, 1], vertical_alignment="center")
    size = p1.segmented_control("בעמוד", [10, 25, 50], default=25, key="page_size", label_visibility="collapsed") or 25
    pages = max(1, -(-n_all // size))
    sig = (q, tuple(f_kw), tuple(f_src), tuple(f_loc), f_min, age, only_new, only_star, only_easy, sort, size, bool(hist))
    if st.session_state.get("page_sig") != sig:
        st.session_state.page_sig, st.session_state.page = sig, 1
    page = min(max(1, st.session_state.get("page", 1)), pages)
    lo, hi = (page - 1) * size, min(page * size, n_all)
    p2.markdown(f"**מציג {lo + 1}–{hi} מתוך {n_all}** · עמוד {page}/{pages}")
    if p3.button("→", key="pg_prev", disabled=page <= 1, width="stretch", help="העמוד הקודם"):
        st.session_state.page = page - 1
        st.rerun()
    if p4.button("←", key="pg_next", disabled=page >= pages, width="stretch", help="העמוד הבא"):
        st.session_state.page = page + 1
        st.rerun()
    page_jobs = shown[lo:hi]
    sel = [j["key"] for j in page_jobs if st.session_state.get(f"sel-{j['key']}")]
    b1, b2, b3, b4 = st.columns([2, 2, 2, 3], vertical_alignment="center")
    if b1.button(f"✓ נשלחו ({len(sel)})", key="sel_sent", disabled=not sel, width="stretch"):
        mark_many(sel, "sent")
        st.rerun()
    if b2.button(f"✕ הסר מסומנות ({len(sel)})", key="rm_sel", disabled=not sel, width="stretch"):
        mark_many(sel, "removed")
        st.rerun()
    if st.session_state.get("rm_all_ask"):
        b4.warning(f"להסיר את כל {len(page_jobs)} המשרות בעמוד?")
        y, n = b4.columns(2)
        if y.button("כן, הסר", key="rm_all_yes", type="primary", width="stretch"):
            st.session_state.pop("rm_all_ask", None)
            mark_many([j["key"] for j in page_jobs], "removed")
            st.rerun()
        if n.button("ביטול", key="rm_all_no", width="stretch"):
            st.session_state.pop("rm_all_ask", None)
            st.rerun()
    elif b3.button(f"הסר הכל בעמוד ({len(page_jobs)})", key="rm_all", width="stretch"):
        st.session_state.rm_all_ask = True
        st.rerun()
    block_actions(sel, "cards")
    for j in page_jobs:
        card(j, new=is_new(j))
    if pages > 1:
        n1, n2, n3 = st.columns([1, 3, 1], vertical_alignment="center")
        if n1.button("→ הקודם", key="pg_prev_b", disabled=page <= 1, width="stretch"):
            st.session_state.page = page - 1
            st.rerun()
        n2.caption(f"עמוד {page} מתוך {pages}")
        if n3.button("הבא ←", key="pg_next_b", disabled=page >= pages, width="stretch"):
            st.session_state.page = page + 1
            st.rerun()


def _preset(keys):
    for i, (g, items) in enumerate(GROUPS.items()):
        st.session_state[f"kwg_{i}"] = [k for k in items if k in keys]


def prefs_panel(s):
    """Assaf 21 Nov: search terms kept apart from keywords: region, employment type, work arrangement, sources. Saved with the data."""
    p = prefs_of(s)
    for key, val in (("pf_regions", p["regions"]), ("pf_emp", p["employment"]), ("pf_arr", p["arrangement"]), ("pf_src", p["sources"]), ("pf_cty", p.get("countries", [])), ("pf_eu_open", p.get("eu_open", True))):
        st.session_state.setdefault(key, val if isinstance(val, bool) else list(val))
    with st.container(border=True):
        st.markdown("##### תנאים ומקורות")
        c1, c2 = st.columns(2)
        regions = c1.pills("אזור", list(REGION_OPTS), format_func=REGION_OPTS.get, selection_mode="multi", key="pf_regions") or []
        arr = c2.pills("אופי עבודה (ריק = הכל)", ARRANGEMENTS, format_func=ARR_HE.get, selection_mode="multi", key="pf_arr") or []
        emp = c1.pills("סוג העסקה (ריק = הכל)", EMP_TYPES, format_func=EMP_HE.get, selection_mode="multi", key="pf_emp") or []

        def _all_src():
            st.session_state.pf_src = list(SOURCES)
        c2.button("בחר את כל המקורות", on_click=_all_src, key="pf_src_all")
        src = st.pills("מקורות", list(SOURCES), format_func=SOURCES.get, selection_mode="multi", key="pf_src") or []
        countries = []
        if "EEA" in regions:
            countries = st.multiselect("מדינות באיחוד האירופי (ריק = כל האיחוד)", sorted(EU_COUNTRIES), key="pf_cty", placeholder="כל מדינות האיחוד האירופי",
                                       help="משרות שהמיקום שלהן מציין רק מדינות אחרות יוסרו בחינם לפני הדירוג. משרות בלי מדינה (מרחוק) נשארות.")
        eu_open = True
        if "EEA" in regions:
            eu_open = st.toggle("🇪🇺 פתוח לעבודה באירופה, לא רק מרחוק מישראל", key="pf_eu_open",
                                help="כבוי: משרות מחוץ לישראל נשארות רק אם הן מרחוק מלא. דלוק: גם משרות היברידיות ובאתר באירופה (אתה אזרח פולני).")
        notes = []
        if not regions:
            notes.append("בחר לפחות אזור אחד.")
        if "EEA" not in regions and set(src) & EEA_SOURCES:
            notes.append("Himalayas הוא לוח של עבודה מרחוק, ורץ רק כשנבחר EEA.")
        if regions == ["EEA"] and set(src) & IL_SOURCES:
            notes.append("AllJobs, JobMaster ודרושים הם לוחות ישראליים, ולא ירוצו כשנבחר רק EEA.")
        if "EEA" in regions:
            notes.append("ב-EEA: LinkedIn (האיחוד האירופי) ודפי הקריירה של 206 החברות עם משרות באירופה. " + ("לפי הפרופיל שלך מותר לך לעבוד באיחוד; " if PF.eu_citizen(profile()) else "") + "בכל משרה נבדק אם המעסיק מאפשר עבודה מהמקום שבו אתה גר.")
        if emp or arr:
            notes.append("הסינון לפי סוג העסקה ואופי עבודה נעשה על ידי Claude שקורא כל משרה (LinkedIn החזיר 0 תוצאות כשהסינון נשלח אליו). מה שלא כתוב במשרה מסומן \"דורש אימות\".")
        for n in notes:
            st.caption(n)
    new = {"regions": regions or ["IL"], "employment": emp, "arrangement": arr, "sources": src or list(SOURCES), "countries": countries, "eu_open": eu_open}
    return new


def save_prefs(new):
    def m(st_):
        settings_of(st_)["search_prefs"] = new
    update_state(m, "search prefs")


def page_search():
    s, _ = load_state()
    cap = 60
    b, info = active_batch()
    running = bool(info and not info["done"])
    if info:
        with st.container(border=True):
            st.fragment(run_every=3 if running else None)(progress_live)()
            if info["done"]:
                c1, c2 = st.columns(2)
                if c1.button("לתיבת המשרות ←", key="go_inbox", type="primary", width="stretch"):
                    st.session_state.only_new = True
                    st.switch_page(PAGES["inbox"])
                if c2.button("סגור", key="close_batch", width="stretch"):
                    st.session_state.pop("batch", None)
                    if b in SH["batches"]:
                        SH["batches"][b]["closed"] = True
                    st.rerun()

    # Assaf 22 Nov: ask Claude about the results of the current / last search
    runs_all = s.get("runs", [])
    grp = b if any((r.get("batch") or r["at"]) == b for r in runs_all) else ((runs_all[0].get("batch") or runs_all[0]["at"]) if runs_all else None)
    if grp:
        gruns = [r for r in runs_all if (r.get("batch") or r["at"]) == grp]
        gkeys = list(dict.fromkeys(k for r in gruns for k in (r.get("keys") or [])))
        if gkeys:
            glabel = ", ".join(lbl(k) for k in dict.fromkeys(r["kw"] for r in gruns)) + " · " + to_il(min(r["at"] for r in gruns)).strftime("%d.%m %H:%M")
            with st.expander(f"✨ שאל את התוצאות · {glabel} ({len(gkeys)} משרות)", expanded=bool(st.session_state.get("ask_search_hist", {}).get(grp))):
                search_ask_panel(grp, gkeys, glabel, s)

    prefs = prefs_panel(s)
    st.markdown("##### מילות חיפוש")
    p = st.columns(5)
    p[0].button(f"⭐ מומלצות ({len(RECOMMENDED)})", on_click=_preset, args=(RECOMMENDED,), width="stretch", type="primary",
                help="המילים שמביאות הכי הרבה הגשות + מילים קטנות עם אחוז שליחה גבוה. כדאי להריץ פעמיים ביום")
    p[1].button("🆕 12 החדשות", on_click=_preset, args=(NEW12,), width="stretch")
    p[2].button("כל המילים", on_click=_preset, args=(KEYWORDS,), width="stretch")
    p[3].button("נקה", on_click=_preset, args=([],), width="stretch")
    picked = []
    for i, (g, items) in enumerate(GROUPS.items()):
        if f"kwg_{i}" not in st.session_state:
            st.session_state[f"kwg_{i}"] = []
        picked += st.pills(g, items, format_func=lbl, selection_mode="multi", key=f"kwg_{i}") or []
    st.session_state.setdefault("kw_free", PF.default_keywords(profile()))  # default extra keywords come from the profile
    free = st.text_input("מילים נוספות (מופרדות בפסיק)", key="kw_free", placeholder="למשל: revops, מנהל CRM")
    kws = list(dict.fromkeys(picked + [w.strip().lower() for w in free.split(",") if w.strip()]))

    used = runs_today(s)
    pending = sum(1 for i in SH["batches"].values() if not i["done"] for v in i["kw"].values() if v.get("phase") not in ("done", "error"))
    left = cap - used - pending
    with st.container(border=True):
        w1, w2 = st.columns([3, 2], vertical_alignment="bottom")
        st.session_state.setdefault("win", 48)
        win = w1.segmented_control("משרות שפורסמו ב-", list(WINDOWS), format_func=lambda h: WINDOWS[h], key="win") or 0
        mins = -(-len(kws) // 3) * 3
        w2.markdown(f"**{len(kws)} מילים** · כ-${len(kws) * USD_PER_KW:.2f} · כ-{mins} דק'  \n"
                    f"<span class='sub'>היום {used + pending}/{cap} חיפושים · נשארו {max(0, left)}</span>", unsafe_allow_html=True)
        over = len(kws) > left
        if over:
            st.warning(f"בחרת {len(kws)} מילים, אבל נשארו היום רק {max(0, left)} חיפושים. הקטן את הבחירה או העלה את MAX_SEARCHES_PER_DAY ב-Secrets.")
        if st.button(f"🔍 חפש {len(kws)} מילים · {WINDOWS[win]}", type="primary", disabled=running or not kws or over, width="stretch"):
            if prefs != prefs_of(s):
                save_prefs(prefs)  # the choices stay for the next visit
            st.session_state.batch = start_batch(kws, win, prefs)
            st.session_state.pop("hist", None)
            st.rerun()
        if running:
            st.caption("חיפוש רץ כבר. אפשר לעבור לתיבת המשרות; משרות נכנסות לשם בכל פעם שמילה מסתיימת.")


def ask_search(rows, label, question, prior):
    """Assaf 21 Nov: ask Claude about ONE search (the jobs it returned). Cheapest model; compact table of the results."""
    import anthropic
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
    lines = ["title|company|fit|status|why"] + [f"{j.get('title')}|{j.get('company')}|{j.get('fit')}|{j['_st']}|{(j.get('why') or '')[:110]}" for j in rows[:400]]
    system = (BRAIN + "\n\n---\nYou answer " + PF.first_name(profile()) + "'s questions about ONE search they ran in Job Scout: " + label + ". Below are the jobs it returned. Answer in simple, "
              "natural Hebrew, male second person, keep tool names in English, concrete and short. Use only this data, compute exactly, say when it cannot "
              "answer.\n\nRESULTS:\n" + "\n".join(lines))
    msgs = []
    for q, a in prior[-4:]:
        msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    msgs.append({"role": "user", "content": question})
    r = client.messages.create(model=MODEL, max_tokens=6000, system=system, messages=msgs)
    track(r)
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip() or "(אין תשובה)"


SEARCH_PRESETS = ["מה הכי שווה לשלוח מהחיפוש הזה?", "למה הרבה משרות קיבלו ציון נמוך כאן?", "אילו חברות חוזרות?"]


def search_ask_panel(g, keys, label, s):
    hist = st.session_state.setdefault("ask_search_hist", {}).setdefault(g, [])
    ST = {"sent": "נשלח", "removed": "הוסר", "auto": "מתחת לסף"}
    rows = [{**s["jobs"][k], "_st": ST.get((s["archive"].get(k) or {}).get("status"), "פתוח")} for k in keys if k in s["jobs"]]
    rows.sort(key=lambda j: -(j.get("fit") or 0))
    with st.container(border=True):
        st.markdown(f"<span class='ai-h'>✨ שאל את Claude על החיפוש הזה ({len(rows)} משרות)</span>", unsafe_allow_html=True)
        for q, a in hist:
            st.markdown(f"**שאלת:** {q}")
            with st.container(border=True):
                st.markdown(a)
        cols = st.columns(len(SEARCH_PRESETS))
        asked = None
        for i, q in enumerate(SEARCH_PRESETS):
            if cols[i].button(q, key=f"sap-{g}-{i}", width="stretch"):
                asked = q
        with st.form(f"saf-{g}", clear_on_submit=True, border=False):
            a1, a2 = st.columns([5, 1], vertical_alignment="bottom")
            free = a1.text_input("שאלה משלך", placeholder="שאלה משלך…", label_visibility="collapsed")
            if a2.form_submit_button("שאל", type="primary", width="stretch") and free.strip():
                asked = free.strip()
        if asked:
            with st.spinner("Claude חושב…"):
                try:
                    hist.append((asked, ask_search(rows, label, asked, hist)))
                except Exception as e:
                    st.info(f"השאלה נכשלה: {_redact(e)[:200]}", icon="⚠️")
                    return
            st.rerun()


def page_history():
    """Assaf 19 Nov: every search ever kept (last 500 keyword runs), not only 72 hours; 'search again' refills the search page."""
    s, _ = load_state()
    groups = {}
    for r in s.get("runs", []):
        groups.setdefault(r.get("batch") or r["at"], []).append(r)
    st.markdown("##### היסטוריית חיפושים")
    st.caption("כל החיפושים שנשמרו. \"פתח\" מציג בתיבה רק את המשרות של החיפוש (זמין 72 שעות). \"↻ חפש שוב\" ממלא את עמוד החיפוש באותן מילים.")
    if not groups:
        st.info("עוד לא היו חיפושים.")
    q = st.text_input("סינון", placeholder="🔎 מילת חיפוש…", key="hist_q", label_visibility="collapsed")
    shown_n = 0
    for g, runs in sorted(groups.items(), key=lambda x: max(r["at"] for r in x[1]), reverse=True):
        kws_g = list(dict.fromkeys(r["kw"] for r in runs))
        if q.strip() and not any(q.strip().lower() in lbl(k).lower() for k in kws_g):
            continue
        shown_n += 1
        if shown_n > 40:
            st.caption("מוצגים 40 החיפושים האחרונים. צמצם בעזרת הסינון כדי לראות ישנים יותר.")
            break
        keys = list(dict.fromkeys(k for r in runs for k in (r.get("keys") or [])))
        open_n = sum(1 for k in keys if k not in s["archive"])
        good = sum(1 for k in keys if ((s["jobs"].get(k) or {}).get("fit") or 0) >= MIN_FIT)
        hours = runs[0].get("hours") or 0
        at = to_il(min(r["at"] for r in runs)).strftime("%d.%m %H:%M")
        usd = sum((r.get("claudeUsd") or 0) for r in runs) + len(runs) * 0.16  # Apify per keyword, measured 18 Nov
        label = f"{at} · {len(runs)} מילים · {WINDOWS.get(hours, 'שבוע')}"
        with st.container(border=True):
            a, b_, c, d_ = st.columns([4.2, 1, 1.2, 1.7], vertical_alignment="center")
            a.markdown(f"**{label}** · {sum(r.get('new', 0) for r in runs)} חדשות"
                       + (f" · {good} עם ציון {MIN_FIT}+ · {open_n} עדיין פתוחות" if keys else "")
                       + f" · כ-${usd:.2f}  \n"
                       + " ".join(f"<span class='tag t-kw'>{lbl(r['kw'])} · {r.get('new', 0)}</span>" for r in runs), unsafe_allow_html=True)
            if b_.button("פתח", key=f"h-{g}", width="stretch", disabled=not open_n,
                         help=None if keys else "רשימת המשרות של חיפוש נשמרת 72 שעות"):
                st.session_state.hist = {"keys": keys, "label": label}
                st.switch_page(PAGES["inbox"])
            if d_.button("✨ שאל את Claude", key=f"sask-{g}", width="stretch", disabled=not keys,
                         help=None if keys else "רשימת המשרות של חיפוש נשמרת 72 שעות"):
                st.session_state.ask_search_open = None if st.session_state.get("ask_search_open") == g else g
                st.rerun()
            if c.button("↻ חפש שוב", key=f"again-{g}", width="stretch"):
                _preset(kws_g)
                st.session_state.kw_free = ", ".join(k for k in kws_g if k not in KEYWORDS)
                st.session_state.win = hours if hours in WINDOWS else 0
                st.switch_page(PAGES["search"])
        if st.session_state.get("ask_search_open") == g:
            search_ask_panel(g, keys, label, s)

    st.markdown("##### לפי מילה")
    have = [k for k in KEYWORDS if any(r["kw"] == k for r in s.get("runs", []))] + \
           sorted({r["kw"] for r in s.get("runs", []) if r["kw"] not in KEYWORDS})
    kw = st.selectbox("מילה", have, format_func=lbl, index=None, placeholder="בחר מילה", label_visibility="collapsed")
    if not kw:
        return
    runs = [r for r in s.get("runs", []) if r["kw"] == kw]
    st.dataframe(pd.DataFrame([{
        "תאריך": to_il(r["at"]).strftime("%d.%m %H:%M"), "חלון": WINDOWS.get(r.get("hours") or 0, "שבוע"), "נמצאו": r.get("found", 0),
        "חדשות": r.get("new", 0), f"מתחת ל-{MIN_FIT}": r.get("autoRemoved", 0),
        "לפי מקור (נכנסו/נשלפו)": " · ".join(f"{BOARD.get(b, b)} {v.get('kept', 0)}/{v.get('pulled', 0)}" for b, v in (r.get("stats") or {}).items()),
    } for r in runs]), hide_index=True, width="stretch")
    ST = {"sent": "נשלח", "removed": "הוסר", "auto": f"מתחת ל-{MIN_FIT}"}
    jobs = [{**j, "_status": ST.get((s["archive"].get(j["key"]) or {}).get("status"), "פתוח")} for j in s["jobs"].values() if kw in j.get("kws", [])]
    st.caption(f"{len(jobs)} משרות נמצאו במילה הזאת")
    job_table(sorted(jobs, key=lambda j: -(j.get("fit") or 0)), key=f"kwjobs_{kw}")


def save_settings(change, message):
    def m(s):
        change(settings_of(s))
    update_state(m, message)


def page_filters():
    """Assaf 19 Nov: words that hide a job (title) + blocked companies. Applied in the inbox now and to every future search (no scoring cost)."""
    s, _ = load_state()
    cfg = settings_of(s)
    opn = [j for j in s["jobs"].values() if j["key"] not in s["archive"] and j["key"] not in s.get("queue", {})]
    st.markdown("##### מילים לסינון")
    st.caption("משרה שהכותרת שלה מכילה אחת מהמילים לא תופיע בתיבה, ובחיפושים הבאים תוסר אוטומטית בלי לשלם על דירוג. "
               "מילים באנגלית נבדקות כמילה שלמה (staff לא יתפוס staffing).")
    with st.form("add_word", clear_on_submit=True, border=True):
        a, b = st.columns([5, 1], vertical_alignment="bottom")
        new = a.text_input("הוסף מילים (מופרדות בפסיק)", placeholder="למשל: Principal, Staff, מועדון לקוחות, נציג")
        if b.form_submit_button("הוסף", type="primary", width="stretch") and new.strip():
            words = [w.strip() for w in new.split(",") if w.strip()]
            save_settings(lambda c: c.__setitem__("exclude_words", list(dict.fromkeys(c["exclude_words"] + words))), f"exclude +{len(words)}")
            st.rerun()
    if cfg["exclude_words"]:
        hits = {w: sum(1 for j in opn if _word_hit(w, j.get("title"))) for w in cfg["exclude_words"]}
        drop = st.pills("לחץ על מילה כדי לסמן אותה להסרה", cfg["exclude_words"], selection_mode="multi", key="drop_words",
                        format_func=lambda w: f"{w} · {hits[w]}")
        if st.button(f"✕ הסר מהרשימה ({len(drop or [])})", disabled=not drop, key="rm_words"):
            save_settings(lambda c: c.__setitem__("exclude_words", [w for w in c["exclude_words"] if w not in drop]), "exclude -words")
            st.rerun()
    else:
        st.info("אין עדיין מילים לסינון.")

    st.markdown("##### מילים שתמיד נשארות")
    st.caption("משרה שהכותרת שלה מכילה אחת מהמילים האלה לא תוסתר, גם אם יש בה מילה לסינון. למשל GTM שומר על GTM AI Engineer.")
    with st.form("add_keep", clear_on_submit=True, border=True):
        a, b = st.columns([5, 1], vertical_alignment="bottom")
        newk = a.text_input("הוסף מילים (מופרדות בפסיק)", placeholder="למשל: GTM, Solutions")
        if b.form_submit_button("הוסף", width="stretch") and newk.strip():
            words = [w.strip() for w in newk.split(",") if w.strip()]
            save_settings(lambda c: c.__setitem__("keep_words", list(dict.fromkeys(c["keep_words"] + words))), f"keep +{len(words)}")
            st.rerun()
    if cfg["keep_words"]:
        dropk = st.pills("לחץ על מילה כדי לסמן אותה להסרה", cfg["keep_words"], selection_mode="multi", key="drop_keep")
        if st.button(f"✕ הסר מהרשימה ({len(dropk or [])})", disabled=not dropk, key="rm_keep"):
            save_settings(lambda c: c.__setitem__("keep_words", [w for w in c["keep_words"] if w not in dropk]), "keep -words")
            st.rerun()

    st.markdown("##### חברות חסומות")
    blocked = cfg["blocked_companies"]
    names = {}
    for j in s["jobs"].values():
        ck = company_key(j.get("company"))
        if ck:
            names.setdefault(ck, j.get("company"))
    if blocked:
        drop_c = st.pills("לחץ על חברה כדי לבטל חסימה", blocked, selection_mode="multi", key="drop_cos", format_func=lambda c: names.get(c, c))
        if st.button(f"בטל חסימה ({len(drop_c or [])})", disabled=not drop_c, key="unblock"):
            save_settings(lambda c: c.__setitem__("blocked_companies", [x for x in c["blocked_companies"] if x not in drop_c]), "unblock companies")
            st.rerun()
    else:
        st.info("אין חברות חסומות. אפשר לחסום חברה בעמוד \"חברות\".")

    hid = [j for j in opn if excluded(s, j)]
    st.markdown("##### מוסתרות עכשיו")
    if not hid:
        st.caption("אין משרות פתוחות שמוסתרות כרגע.")
        return
    st.caption(f"{len(hid)} משרות פתוחות מוסתרות מהתיבה. אפשר להעביר אותן לארכיון כדי לנקות.")
    job_table(hid, key="hidden_tbl", height=min(38 + 35 * len(hid), 360))
    if st.button(f"העבר את כל {len(hid)} לארכיון (הוסרו)", key="hide_to_arc"):
        mark_many([j["key"] for j in hid], "removed")
        st.rerun()


def page_companies():
    """Assaf 19 Nov: every company seen, with jobs / open / sent; follow (⭐ filter in the inbox) or block (hidden + skipped in searches)."""
    s, _ = load_state()
    cfg = settings_of(s)
    cos = {}
    for k, j in s["jobs"].items():
        ck = company_key(j.get("company"))
        if not ck:
            continue
        c = cos.setdefault(ck, {"key": ck, "name": j.get("company"), "jobs": 0, "open": 0, "sent": 0, "removed": 0, "last_sent": "", "best": 0})
        c["jobs"] += 1
        a = s["archive"].get(k)
        if not a:
            c["open"] += 1
        elif a.get("status") == "sent":
            c["sent"] += 1
            c["last_sent"] = max(c["last_sent"], a.get("at", ""))
        elif a.get("status") == "removed":
            c["removed"] += 1
        c["best"] = max(c["best"], j.get("fit") or 0)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("חברות", len(cos))
    m2.metric("עם משרות פתוחות", sum(1 for c in cos.values() if c["open"]))
    m3.metric("שלחת אליהן", sum(1 for c in cos.values() if c["sent"]))
    m4.metric("במעקב · חסומות", f"{len(cfg['followed_companies'])} · {len(cfg['blocked_companies'])}")
    f1, f2 = st.columns([3, 2], vertical_alignment="center")
    q = f1.text_input("חיפוש", placeholder="🔎 שם חברה…", key="co_q", label_visibility="collapsed")
    show = f2.segmented_control("הצג", ["all", "open", "sent", "star", "blocked"], default="all", key="co_show", label_visibility="collapsed",
                                format_func={"all": "הכל", "open": "עם פתוחות", "sent": "שלחתי", "star": "⭐ במעקב", "blocked": "🚫 חסומות"}.get) or "all"
    rows = sorted(cos.values(), key=lambda c: (-c["open"], -c["sent"], -c["jobs"]))
    if q.strip():
        rows = [c for c in rows if q.strip().lower() in str(c["name"]).lower()]
    rows = {"all": rows, "open": [c for c in rows if c["open"]], "sent": [c for c in rows if c["sent"]],
            "star": [c for c in rows if c["key"] in cfg["followed_companies"]],
            "blocked": [c for c in rows if c["key"] in cfg["blocked_companies"]]}[show]
    if not rows:
        st.info("אין חברות להצגה.")
        return
    df = pd.DataFrame([{"": ("⭐" if c["key"] in cfg["followed_companies"] else "") + ("🚫" if c["key"] in cfg["blocked_companies"] else ""),
                        "חברה": c["name"], "משרות": c["jobs"], "פתוחות": c["open"], "שלחת": c["sent"], "הסרת": c["removed"],
                        "ציון הכי גבוה": c["best"], "הגשה אחרונה": to_il(c["last_sent"]).strftime("%d.%m") if c["last_sent"] else ""} for c in rows])
    ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row", key="co_tbl",
                      height=min(38 + 35 * len(rows), 560),
                      column_config={"ציון הכי גבוה": st.column_config.ProgressColumn("ציון הכי גבוה", min_value=0, max_value=100, format="%d")})
    sel = [rows[i]["key"] for i in (ev.selection.rows if ev and ev.selection else [])]
    b1, b2, b3, b4 = st.columns(4)
    if b1.button(f"⭐ עקוב ({len(sel)})", disabled=not sel, width="stretch", type="primary"):
        save_settings(lambda c: c.__setitem__("followed_companies", list(dict.fromkeys(c["followed_companies"] + sel))), f"follow x{len(sel)}")
        st.rerun()
    if b2.button(f"בטל מעקב ({len(sel)})", disabled=not sel, width="stretch"):
        save_settings(lambda c: c.__setitem__("followed_companies", [x for x in c["followed_companies"] if x not in sel]), f"unfollow x{len(sel)}")
        st.rerun()
    if b3.button(f"🚫 חסום ({len(sel)})", disabled=not sel, width="stretch", help="המשרות שלהן יוסתרו, ובחיפושים הבאים יוסרו אוטומטית"):
        save_settings(lambda c: c.__setitem__("blocked_companies", list(dict.fromkeys(c["blocked_companies"] + sel))), f"block x{len(sel)}")
        st.rerun()
    if b4.button(f"בטל חסימה ({len(sel)})", disabled=not sel, width="stretch"):
        save_settings(lambda c: c.__setitem__("blocked_companies", [x for x in c["blocked_companies"] if x not in sel]), f"unblock x{len(sel)}")
        st.rerun()


def page_archive():
    s, _ = load_state()
    rows = {"sent": [], "removed": [], "auto": []}
    for k, a in s["archive"].items():
        rows.setdefault(a.get("status", "removed"), []).append({**s["jobs"].get(k, {}), **a, "key": k})
    q = st.text_input("חיפוש", placeholder="🔎 חברה או תפקיד…", key="arc_q", label_visibility="collapsed")
    tabs = st.tabs([f"✓ נשלחו ({len(rows['sent'])})", f"✕ הוסרו ({len(rows['removed'])})", f"⤓ ציון נמוך ({len(rows['auto'])})"])
    for tab, st_ in zip(tabs, ["sent", "removed", "auto"]):
        with tab:
            lst = sorted(rows[st_], key=lambda j: j.get("at", ""), reverse=True)
            if q.strip():
                ql = q.strip().lower()
                lst = [j for j in lst if ql in str(j.get("title", "")).lower() or ql in str(j.get("company", "")).lower()]
            if st_ == "auto":
                st.caption(f"משרות שקיבלו ציון מתחת ל-{MIN_FIT} ולא הוצגו. אפשר להחזיר לתיבה.")
            if not lst:
                st.info("אין כאן משרות.")
                continue
            sel = job_table(lst, key=f"arc_{st_}", height=560)
            if st.button(f"↩ החזר לתיבה ({len(sel)})", key=f"restore_{st_}", disabled=not sel):
                restore_many(sel)
                st.rerun()


def page_low():
    """Assaf 21 Nov: jobs that scored under MIN_FIT, to review by hand and send anyway if he wants."""
    s, _ = load_state()
    cfg = settings_of(s)
    CTX["followed"] = set(cfg["followed_companies"])
    CTX["sent_by_co"] = {}
    low = []
    for k, a in s["archive"].items():
        j = s["jobs"].get(k)
        if a.get("status") == "auto" and j and j.get("fit") is not None and j["fit"] < MIN_FIT and a.get("reason") not in ("cyber", "excluded"):
            low.append(j)
    undo_bar()
    st.caption(f"משרות שקיבלו ציון מתחת ל-{MIN_FIT}. עבור עליהן ידנית. ✓ שלחתי מסמן ושולח לארכיון, ו-↩ מחזיר לתיבה הראשית למרות הציון. "
               f"משרות סייבר ומילים מסוננות לא מופיעות כאן.")
    if not low:
        st.info("אין משרות מתחת לסף.")
        return
    f1, f2, f3 = st.columns([3, 1.6, 2], vertical_alignment="center")
    q = f1.text_input("חיפוש", placeholder="🔎 חברה או תפקיד…", key="low_q", label_visibility="collapsed")
    lo_min = f2.selectbox("מציון", [0, 20, 30], format_func=lambda v: "כל הציונים" if not v else f"{v}+", key="low_min", label_visibility="collapsed")
    view = f3.segmented_control("תצוגה", ["cards", "table"], format_func={"cards": "▦ כרטיסים", "table": "☰ טבלה"}.get, default="cards",
                                key="low_view", label_visibility="collapsed") or "cards"
    if q.strip():
        ql = q.strip().lower()
        low = [j for j in low if ql in str(j.get("title", "")).lower() or ql in str(j.get("company", "")).lower()]
    low = sorted([j for j in low if (j.get("fit") or 0) >= lo_min], key=lambda j: (-(j.get("fit") or 0), str(j.get("company") or "")))
    st.markdown(f"**{len(low)} משרות**")
    if view == "table":
        sel = job_table(low, key="tbl_low", height=620)
        t1, t2, t3 = st.columns(3)
        if t1.button(f"✓ שלחתי ({len(sel)})", key="low_t_sent", disabled=not sel, type="primary", width="stretch"):
            mark_many(sel, "sent")
            st.rerun()
        if t2.button(f"↩ החזר לתיבה ({len(sel)})", key="low_t_back", disabled=not sel, width="stretch"):
            force_back(sel)
            st.rerun()
        if t3.button(f"✕ הסר סופית ({len(sel)})", key="low_t_rm", disabled=not sel, width="stretch"):
            mark_many(sel, "removed")
            st.rerun()
        return
    for j in low[:60]:
        card(j)
        if st.button("↩ החזר לתיבה הראשית", key=f"fb-{j['key']}"):
            force_back([j["key"]])
            st.rerun()
    if len(low) > 60:
        st.caption(f"מוצגות 60 הראשונות מתוך {len(low)}. צמצם בחיפוש או עבור לתצוגת הטבלה.")


def _pct(a, b):
    return round(100 * a / b) if b else 0


def stats_tables(s):
    """Per keyword / per source tables with counts and percentages (Assaf 19 Nov). Also feeds 'Ask the data'."""
    arc, jobs = s["archive"], s["jobs"]
    status = lambda j: (arc.get(j["key"]) or {}).get("status") or "open"
    kw_rows = []
    for k in list(dict.fromkeys(KEYWORDS + [r["kw"] for r in s.get("runs", [])])):
        runs = [r for r in s.get("runs", []) if r["kw"] == k]
        js = [j for j in jobs.values() if k in j.get("kws", [])]
        if not runs and not js:
            continue
        scored = [j for j in js if j.get("fit") is not None]
        g45 = sum(1 for j in scored if j["fit"] >= MIN_FIT)
        sent = sum(1 for j in js if status(j) == "sent")
        kw_rows.append({"מילה": lbl(k), "חיפושים": len(runs), "משרות": len(js), "ציון 40+": g45, "% 40+": _pct(g45, len(scored)),
                        "ציון 70+": sum(1 for j in scored if j["fit"] >= 70), "% 70+": _pct(sum(1 for j in scored if j["fit"] >= 70), len(scored)),
                        "נשלחו": sent, "% נשלחו מ-40+": _pct(sent, g45), "הוסרו": sum(1 for j in js if status(j) == "removed"),
                        "% מסך הנשלחות": 0})
    total_sent = sum(1 for a in arc.values() if a.get("status") == "sent") or 1
    for r in kw_rows:
        r["% מסך הנשלחות"] = _pct(r["נשלחו"], total_sent)
    src = {}
    for j in jobs.values():
        for b in j.get("boards", []):
            o = src.setdefault(b, {"מקור": BOARD.get(b, b), "משרות": 0, "_scored": 0, "ציון 40+": 0, "נשלחו": 0})
            o["משרות"] += 1
            o["_scored"] += j.get("fit") is not None
            o["ציון 40+"] += (j.get("fit") or 0) >= MIN_FIT
            o["נשלחו"] += status(j) == "sent"
    src_rows = []
    for o in sorted(src.values(), key=lambda o: -o["נשלחו"]):
        src_rows.append({"מקור": o["מקור"], "משרות": o["משרות"], "ציון 40+": o["ציון 40+"], "% 40+": _pct(o["ציון 40+"], o["_scored"]),
                         "נשלחו": o["נשלחו"], "% נשלחו מ-40+": _pct(o["נשלחו"], o["ציון 40+"]), "% מסך הנשלחות": _pct(o["נשלחו"], total_sent)})
    return kw_rows, src_rows


PCT = lambda name: st.column_config.ProgressColumn(name, min_value=0, max_value=100, format="%d%%")
DATA_PRESETS = ["אילו מילות חיפוש הכי משתלמות לי, ואילו כדאי להוריד?", "מאילו מקורות הגיעו המשרות ששלחתי?",
                "כמה שלחתי השבוע לעומת השבוע שעבר?", "לאילו חברות כדאי לי לעקוב, לפי מה ששלחתי והציונים?"]


def data_context(s):
    """Compact text snapshot of the whole dataset for 'Ask the data' (cached by Claude between questions)."""
    kw_rows, src_rows = stats_tables(s)
    arc = s["archive"]
    lines = ["key|title|company|fit|status|keywords|sources|posted|found|archived_at|lang|verdict"]
    for k, j in sorted(s["jobs"].items(), key=lambda x: x[1].get("foundAt") or ""):
        a = arc.get(k) or {}
        lines.append("|".join(str(x or "").replace("|", "/").replace("\n", " ") for x in [
            k[:20], str(j.get("title"))[:80], str(j.get("company"))[:40], j.get("fit"),
            (a.get("status") or "open") + (f":{a['reason']}" if a.get("reason") else ""), ",".join(j.get("kws", [])),
            ",".join(j.get("boards", [])), str(j.get("posted") or "")[:10], str(j.get("foundAt") or "")[:10], str(a.get("at") or "")[:10],
            j.get("lang"), j.get("verdict")]))
    runs = ["date|keyword|window_h|found|new|auto_removed_under_threshold"] + [
        f"{str(r['at'])[:16]}|{r['kw']}|{r.get('hours') or 168}|{r.get('found', 0)}|{r.get('new', 0)}|{r.get('autoRemoved', 0)}" for r in s.get("runs", [])]
    cfg = settings_of(s)
    return "\n".join([
        f"TODAY (Israel): {dt.datetime.now(IL).date()}", f"SCORE THRESHOLD: jobs scored under {MIN_FIT} are archived automatically (status auto).",
        "STATUS VALUES: open = still in his inbox, sent = he applied, removed = he dismissed it, auto = score under the threshold; "
        "auto:cyber = cyber/security employer (his rule: never cyber, whatever the score); auto:excluded = his exclude words or blocked companies.",
        f"EXCLUDE WORDS: {cfg['exclude_words']}", f"BLOCKED COMPANIES: {cfg['blocked_companies']}", f"FOLLOWED COMPANIES: {cfg['followed_companies']}",
        "", "PER KEYWORD:", pd.DataFrame(kw_rows).to_csv(index=False), "PER SOURCE:", pd.DataFrame(src_rows).to_csv(index=False),
        "", f"SEARCH RUNS ({len(runs) - 1}):", "\n".join(runs), "", f"ALL JOBS ({len(lines) - 1}):", "\n".join(lines)])


def ask_data(question, prior):
    import anthropic
    s, _ = load_state()
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
    system = [{"type": "text", "text": (
        "You are the analyst inside Job Scout, the user's job-search dashboard (Israel only; he applies manually; target 50-70 applications a day). "
        "Answer his questions from the data below only - count and compute exactly, show the numbers you used, and say when the data cannot answer. "
        "Answer in simple, natural Hebrew, male second person, short (max ~180 words), using a small markdown table when it helps. "
        "Keep keyword, tool, company and source names as written in the data.\n\nDATA:\n" + data_context(s)),
        "cache_control": {"type": "ephemeral"}}]
    msgs = []
    for q, a in prior[-4:]:
        msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    msgs.append({"role": "user", "content": question})
    r = client.beta.messages.create(model=MODEL, max_tokens=16000, system=system, messages=msgs,
                                    output_config={"effort": "medium"}, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    track(r)
    if r.stop_reason == "refusal":
        return "Claude לא ענה על השאלה הזאת. נסה לנסח אותה אחרת."
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip() or "(אין תשובה)"


def ask_data_panel():
    hist = st.session_state.setdefault("ask_data_hist", [])
    with st.container(border=True):
        st.markdown("<span class='ai-h'>✨ שאל את הנתונים</span> <span class='sub'>Claude עונה מכל המשרות, החיפושים והסטטוסים · כ-$0.05–0.10 לשאלה</span>",
                    unsafe_allow_html=True)
        for q, a in hist:
            st.markdown(f"**שאלת:** {q}")
            with st.container(border=True):
                st.markdown(a)
        cols = st.columns(len(DATA_PRESETS))
        asked = None
        for i, q in enumerate(DATA_PRESETS):
            if cols[i].button(q, key=f"adp-{i}", width="stretch"):
                asked = q
        with st.form("ask_data_form", clear_on_submit=True, border=False):
            a1, a2 = st.columns([5, 1], vertical_alignment="bottom")
            free = a1.text_input("שאלה", placeholder="למשל: כמה משרות עם ציון 70+ הגיעו מ-AllJobs השבוע?", label_visibility="collapsed")
            if a2.form_submit_button("שאל", type="primary", width="stretch") and free.strip():
                asked = free.strip()
        if hist and st.button("נקה שיחה", key="ad_clear"):
            st.session_state.ask_data_hist = []
            st.rerun()
        if asked:
            with st.spinner("Claude מנתח את הנתונים…"):
                try:
                    hist.append((asked, ask_data(asked, hist)))
                except Exception as e:
                    st.info(f"השאלה נכשלה: {_redact(e)[:200]}", icon="⚠️")
                    return
            st.rerun()


def compute_insights(s):
    """Assaf 19 Nov: plain-number conclusions for the Stats page (no AI, no cost)."""
    arc, jobs = s["archive"], s["jobs"]
    status = lambda j: (arc.get(j["key"]) or {}).get("status") or "open"
    scored = [j for j in jobs.values() if j.get("fit") is not None]
    g45 = [j for j in scored if j["fit"] >= MIN_FIT]
    sent = [j for j in jobs.values() if status(j) == "sent"]
    removed = [j for j in jobs.values() if status(j) == "removed"]
    today = dt.datetime.now(IL).date()
    sent_dates = [to_il(a["at"]).date() for a in arc.values() if a.get("status") == "sent" and a.get("at")]
    last7 = sum(1 for d in sent_dates if d > today - dt.timedelta(days=7))
    active_days = len({d for d in sent_dates if d > today - dt.timedelta(days=7)}) or 1
    goal = int(secret("DAILY_GOAL", 50))
    runs = s.get("runs", [])
    usd = sum((r.get("claudeUsd") or 0) for r in runs) + 0.16 * len(runs)
    kw_rows, src_rows = stats_tables(s)
    strong = sorted([r for r in kw_rows if r["נשלחו"] >= 3], key=lambda r: (-r["% 40+"], -r["נשלחו"]))[:3]
    weak = sorted([r for r in kw_rows if r["משרות"] >= 8 and r["% 40+"] < 35], key=lambda r: r["% 40+"])[:3]
    dead = [r["מילה"] for r in kw_rows if r["חיפושים"] >= 1 and r["ציון 40+"] == 0]
    best_src = max(src_rows, key=lambda r: r["% מסך הנשלחות"]) if src_rows else None
    eff_src = max([r for r in src_rows if r["ציון 40+"] >= 5] or src_rows or [{}], key=lambda r: r.get("% 40+", 0)) if src_rows else None
    bands = []
    for lo, hi in [(MIN_FIT, 55), (55, 65), (65, 75), (75, 101)]:
        b = [j for j in scored if lo <= j["fit"] < hi and status(j) in ("sent", "removed")]
        bands.append((f"{lo}–{min(hi, 100) - (0 if hi > 100 else 1)}", len(b), _pct(sum(1 for j in b if status(j) == "sent"), len(b))))
    eng = re.compile(r"\b(engineer|developer|software|scientist)\b", re.I)
    low = [j for j in scored if j["fit"] < MIN_FIT]
    eng_low = sum(1 for j in low if eng.search(str(j.get("title"))) and not is_gtm(j))
    return {"scored": len(scored), "g45": len(g45), "sent": len(sent), "removed": len(removed), "found": len(jobs), "low": len(low),
            "eng_low": eng_low, "last7": last7, "per_day": round(last7 / active_days, 1), "active_days": active_days, "goal": goal,
            "usd": usd, "usd_per_sent": usd / max(1, len(sent)), "strong": strong, "weak": weak, "dead": dead,
            "best_src": best_src, "eff_src": eff_src, "bands": bands}


def insights_block(s):
    i = compute_insights(s)
    st.markdown("##### 💡 תובנות ומסקנות")
    st.caption("מחושב אוטומטית מהנתונים שלך, בלי עלות. מתעדכן בכל כניסה.")
    f1, f2, f3, f4, f5 = st.columns(5)
    f1.metric("נמצאו", i["found"])
    f2.metric("דורגו", i["scored"])
    f3.metric(f"ציון {MIN_FIT}+", i["g45"], f"{_pct(i['g45'], i['scored'])}% מהמדורגות", delta_color="off")
    f4.metric("נשלחו", i["sent"], f"{_pct(i['sent'], i['g45'])}% מהטובות", delta_color="off")
    f5.metric("עלות להגשה", f"${i['usd_per_sent']:.2f}", f"${i['usd']:.0f} בסך הכל", delta_color="off")
    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown("**🎯 קצב מול היעד**")
        gap = i["goal"] - i["per_day"]
        st.markdown(f"ב-7 הימים האחרונים שלחת **{i['last7']}** הגשות ב-{i['active_days']} ימי עבודה: **{i['per_day']} ביום** מול יעד של {i['goal']}. "
                    + (f"חסרות כ-**{gap:.0f} הגשות ביום**. כדי לסגור את הפער צריך כ-{(gap / max(0.01, _pct(i['sent'], i['g45']) / 100)):.0f} משרות טובות נוספות ביום, "
                       f"כי אתה שולח ל-{_pct(i['sent'], i['g45'])}% מהמשרות הטובות." if gap > 0 else "אתה עומד ביעד. 👏"))
    with c2.container(border=True):
        st.markdown("**📉 למה רוב המשרות לא מתאימות**")
        st.markdown(f"**{_pct(i['low'], i['scored'])}%** מהמשרות קיבלו ציון מתחת ל-{MIN_FIT}. "
                    f"**{i['eng_low']}** מהן הן תפקידי פיתוח או הנדסה, שכבר מסוננים בעמוד \"סינון\". "
                    "שאר הסיבות שחוזרות בהסברים: דרישת שנות ניסיון ובכירות, ותחום מקצועי שאינו שלך.")
    c3, c4 = st.columns(2)
    with c3.container(border=True):
        st.markdown("**✅ מילות החיפוש הכי משתלמות**")
        st.markdown("\n".join(f"- **{r['מילה']}**: {r['% 40+']}% מהמשרות עם ציון טוב, {r['נשלחו']} הגשות" for r in i["strong"]) or "אין מספיק נתונים עדיין.")
        if i["weak"]:
            st.markdown("**⚠️ חלשות:** " + " · ".join(f"{r['מילה']} ({r['% 40+']}% טובות)" for r in i["weak"]))
        if i["dead"]:
            st.markdown("**❌ לא הביאו אף משרה טובה:** " + ", ".join(i["dead"]))
    with c4.container(border=True):
        st.markdown("**📡 מקורות**")
        if i["best_src"]:
            st.markdown(f"- הכי הרבה הגשות: **{i['best_src']['מקור']}** ({i['best_src']['% מסך הנשלחות']}% מכל ההגשות)")
        if i["eff_src"]:
            st.markdown(f"- הכי מדויק: **{i['eff_src'].get('מקור')}** ({i['eff_src'].get('% 40+')}% מהמשרות שלו עם ציון טוב)")
        st.markdown("**🎚️ אחוז הגשה לפי ציון** (מתוך משרות שסימנת)")
        st.markdown(" · ".join(f"{b}: **{p}%** ({n})" for b, n, p in i["bands"]))
        top = i["bands"][-1][2]
        low_b = i["bands"][0][2]
        if top and low_b < top / 2:
            st.caption(f"אתה שולח הרבה יותר למשרות 75+ ({top}%) מאשר ל-{MIN_FIT}–54 ({low_b}%).")


RESEARCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["headline", "kpis", "works", "not_working", "patterns", "keyword_actions", "recommendations", "open_questions"],
    "properties": {
        "headline": {"type": "string"},
        "kpis": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["label", "value", "note", "tone"],
                 "properties": {"label": {"type": "string"}, "value": {"type": "string"}, "note": {"type": "string"},
                                "tone": {"type": "string", "enum": ["good", "warn", "info"]}}}},
        "works": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["title", "detail", "metric"],
                  "properties": {"title": {"type": "string"}, "detail": {"type": "string"}, "metric": {"type": "string"}}}},
        "not_working": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["title", "detail", "metric"],
                        "properties": {"title": {"type": "string"}, "detail": {"type": "string"}, "metric": {"type": "string"}}}},
        "patterns": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["title", "detail"],
                     "properties": {"title": {"type": "string"}, "detail": {"type": "string"}}}},
        "keyword_actions": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["keyword", "action", "reason"],
                            "properties": {"keyword": {"type": "string"}, "action": {"type": "string", "enum": ["boost", "keep", "watch", "drop"]},
                                           "reason": {"type": "string"}}}},
        "recommendations": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["action", "why", "impact", "effort"],
                            "properties": {"action": {"type": "string"}, "why": {"type": "string"},
                                           "impact": {"type": "string", "enum": ["high", "medium", "low"]},
                                           "effort": {"type": "string", "enum": ["low", "medium", "high"]}}}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
}
TONE = {"good": (PAL["green_l"], PAL["green_ink"]), "warn": ("#fdf3e1", "#8a5a00"), "info": (PAL["blue_l"], PAL["blue_ink"])}
ACTION = {"boost": ("⬆ להגביר", PAL["green_l"], PAL["green_ink"]), "keep": ("✓ להשאיר", PAL["blue_l"], PAL["blue_ink"]),
          "watch": ("👁 לעקוב", "#fdf3e1", "#8a5a00"), "drop": ("⬇ להוריד", "#eef0f3", "#5b6474")}
LEVEL = {"high": "גבוהה", "medium": "בינונית", "low": "נמוכה"}


def run_research(s):
    import anthropic
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
    i = compute_insights(s)
    prompt = (
        "Write a research report for the user about their job search, from the data. Fill the JSON fields:\n"
        "- headline: one sentence, the main conclusion.\n- kpis: 4-6 key numbers (value short, e.g. '50%', '36.5/day'; note = what it means; tone good/warn/info).\n"
        "- works: 3-5 things that work (keywords, sources, job types) with the number behind each in metric.\n"
        "- not_working: 3-5 things that don't work and why, each with its number.\n"
        "- patterns: 3-5 patterns in what he sends vs removes (titles, companies, score bands, Hebrew vs English, recruiters).\n"
        "- keyword_actions: one entry per search keyword that has data: boost / keep / watch / drop, with a short reason and number.\n"
        "- recommendations: 5-7 concrete actions, each with why (the number), impact and effort.\n"
        "- open_questions: 2-3 things the data cannot answer.\n"
        f"Daily goal: {i['goal']} applications. Score threshold: {MIN_FIT}. Be specific, use the real numbers, no generic advice. "
        "All text in simple, natural Hebrew, male second person; keep keyword, company and source names as in the data.")
    system = [{"type": "text", "text": (
        "You are the analyst inside Job Scout, the user's job-search dashboard (Israel only; he applies manually). Use only the data below; "
        "compute exactly and say when the data cannot answer. Jobs with GTM in the title or post skip his title filters by his rule.\n\nDATA:\n"
        + data_context(s)), "cache_control": {"type": "ephemeral"}}]
    with client.beta.messages.stream(model=MODEL, max_tokens=64000, system=system, messages=[{"role": "user", "content": prompt}],
                                     output_config={"effort": "high", "format": {"type": "json_schema", "schema": RESEARCH_SCHEMA}},
                                     betas=["server-side-fallback-2026-07-01"], fallbacks="default") as stream:
        r = stream.get_final_message()
    track(r)
    if r.stop_reason == "refusal":
        raise RuntimeError("Claude declined to write the report")
    text = next((b.text for b in r.content if getattr(b, "type", "") == "text"), "")
    if not text:
        raise RuntimeError(f"empty report (stop_reason={r.stop_reason})")
    return json.loads(text)


def _card(bg, fg, title, body, top=""):
    title, body, top = E(str(title)), E(str(body)), E(str(top))
    return (f"<div style='background:{bg};border-radius:12px;padding:10px 12px;height:100%'>"
            + (f"<div style='font-size:.75rem;color:{fg};opacity:.85'>{top}</div>" if top else "")
            + f"<div style='font-weight:700;color:{fg};font-size:1.05rem'>{title}</div>"
            f"<div style='font-size:.85rem;color:#374151;margin-top:3px'>{body}</div></div>")


def research_charts(s):
    """Charts drawn from the data itself (exact numbers), placed next to the report sections."""
    i = compute_insights(s)
    kw_rows, src_rows = stats_tables(s)
    funnel = pd.DataFrame({"שלב": ["1 נמצאו", "2 דורגו", f"3 ציון {MIN_FIT}+", "4 נשלחו"],
                           "משרות": [i["found"], i["scored"], i["g45"], i["sent"]]}).set_index("שלב")
    kw = pd.DataFrame([{"מילה": r["מילה"], "נשלחו": r["נשלחו"], f"% {MIN_FIT}+": r[f"% {MIN_FIT}+"]} for r in kw_rows if r["משרות"] >= 3])
    src = pd.DataFrame([{"מקור": r["מקור"], "נשלחו": r["נשלחו"], f"% {MIN_FIT}+": r[f"% {MIN_FIT}+"]} for r in src_rows])
    bands = pd.DataFrame([{"ציון": b, "% שנשלחו": p} for b, n, p in i["bands"]]).set_index("ציון")
    return funnel, kw, src, bands


def research_block(s):
    """Claude research report, shown as cards + charts (Assaf 19 Nov). Saved in state.json until refreshed. ~$0.15 a report."""
    rep = s.get("research") or {}
    st.markdown("##### 🔬 מחקר מעמיק")
    a, b = st.columns([5, 1.4], vertical_alignment="center")
    a.caption((("עודכן " + to_il(rep["at"]).strftime("%d.%m %H:%M") + " · ") if rep.get("at") else "")
              + "Claude קורא את כל המשרות, הציונים, החיפושים והסטטוסים. הגרפים מחושבים ישירות מהנתונים. כ-$0.15 לדוח.")
    if b.button("🔬 צור דוח חדש" if rep else "🔬 צור דוח", key="make_research", type="primary", width="stretch"):
        with st.spinner("Claude כותב את הדוח… (כדקה)"):
            try:
                data = run_research(s)
            except Exception as e:
                st.info(f"הדוח נכשל: {_redact(e)[:200]}", icon="⚠️")
                return
        update_state(lambda st_: st_.__setitem__("research", {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "data": data}), "research report")
        st.rerun()
    d = rep.get("data")
    if not d:
        if rep.get("text"):  # older text-only report
            with st.container(border=True):
                st.markdown(rep["text"])
            st.caption("זה דוח בפורמט הישן. \"צור דוח חדש\" יציג אותו עם גרפים.")
        else:
            st.info("עוד אין דוח. לחץ \"צור דוח\".")
        return
    funnel, kw, src, bands = research_charts(s)
    st.markdown(f"<div style='background:var(--js-blue-l);border-right:5px solid var(--js-blue);border-radius:10px;padding:12px 16px;font-size:1.05rem;"
                f"font-weight:600;color:#1d3f8f;margin:6px 0 12px'>🧭 {E(str(d['headline']))}</div>", unsafe_allow_html=True)
    if d["kpis"]:
        cols = st.columns(len(d["kpis"][:6]))
        for c, k in zip(cols, d["kpis"][:6]):
            bg, fg = TONE.get(k["tone"], TONE["info"])
            c.markdown(_card(bg, fg, k["value"], k["note"], k["label"]), unsafe_allow_html=True)

    st.markdown("###### המשפך")
    st.bar_chart(funnel, horizontal=True, color=PAL["blue"], height=190)

    t1, t2, t3, t4 = st.tabs(["✅ מה עובד", "⚠️ מה לא עובד", "🔁 דפוסים", "🎯 המלצות"])
    with t1:
        c1, c2 = st.columns([1, 1.2])
        for w in d["works"]:
            c1.markdown(_card(PAL["green_l"], PAL["green_ink"], w["title"], w["detail"], w["metric"]), unsafe_allow_html=True)
            c1.write("")
        if not kw.empty:
            c2.caption("הגשות לפי מילת חיפוש")
            c2.bar_chart(kw.set_index("מילה")[["נשלחו"]].sort_values("נשלחו"), horizontal=True, color=PAL["green"], height=360)
        if not src.empty:
            c2.caption("הגשות לפי מקור")
            c2.bar_chart(src.set_index("מקור")[["נשלחו"]].sort_values("נשלחו"), horizontal=True, color=PAL["blue"], height=200)
    with t2:
        c1, c2 = st.columns([1, 1.2])
        for w in d["not_working"]:
            c1.markdown(_card("#fdf3e1", "#8a5a00", w["title"], w["detail"], w["metric"]), unsafe_allow_html=True)
            c1.write("")
        if not kw.empty:
            c2.caption(f"אחוז המשרות עם ציון {MIN_FIT}+ לפי מילה (נמוך = הרבה רעש)")
            c2.bar_chart(kw.set_index("מילה")[[f"% {MIN_FIT}+"]].sort_values(f"% {MIN_FIT}+"), horizontal=True, color=PAL["blue"], height=360)
    with t3:
        c1, c2 = st.columns([1, 1.2])
        for p in d["patterns"]:
            c1.markdown(_card(PAL["blue_l"], PAL["blue_ink"], p["title"], p["detail"]), unsafe_allow_html=True)
            c1.write("")
        c2.caption("כמה אחוז מהמשרות שסימנת שלחת, לפי ציון")
        c2.bar_chart(bands, color=PAL["blue"], height=260)
    with t4:
        order = {"high": 0, "medium": 1, "low": 2}
        for r in sorted(d["recommendations"], key=lambda r: (order.get(r["impact"], 3), order.get(r["effort"], 3) * -1)):
            bg, fg = TONE["good"] if r["impact"] == "high" else TONE["info"] if r["impact"] == "medium" else ("#eef0f3", "#5b6474")
            st.markdown(_card(bg, fg, r["action"], r["why"],
                              f"השפעה {LEVEL.get(r['impact'], r['impact'])} · מאמץ {LEVEL.get(r['effort'], r['effort'])}"), unsafe_allow_html=True)
            st.write("")
        if d["keyword_actions"]:
            st.markdown("###### מה לעשות עם כל מילת חיפוש")
            html = "".join(
                f"<div style='display:flex;gap:10px;align-items:center;padding:5px 0;border-bottom:1px solid #eef0f3'>"
                f"<span style='min-width:92px;text-align:center;background:{ACTION[x['action']][1]};color:{ACTION[x['action']][2]};"
                f"border-radius:999px;padding:1px 8px;font-size:.78rem'>{ACTION[x['action']][0]}</span>"
                f"<b style='min-width:170px'>{E(str(x['keyword']))}</b><span style='color:#4b5563;font-size:.86rem'>{E(str(x['reason']))}</span></div>"
                for x in sorted(d["keyword_actions"], key=lambda x: ["boost", "keep", "watch", "drop"].index(x["action"])))
            st.markdown(html, unsafe_allow_html=True)
    if d["open_questions"]:
        with st.expander("❓ שאלות פתוחות שהנתונים לא עונים עליהן"):
            st.markdown("\n".join(f"- {q}" for q in d["open_questions"]))


OPT_LOG = [  # Assaf 19 Nov: the changes made to the search, shown as before -> after on the Stats page
    ("AllJobs", "נכשל, 0 משרות (נגמר הזמן)", "תוקן: 40 משרות, עד 180 שניות", "18.11"),
    ("דפי קריירה", "45 חברות · Comeet נשלח לדירוג בלי תיאור", "206 חברות · Comeet עם תיאור מלא", "18–19.11"),
    ("JobMaster", "לא מחובר", "מחובר · 70 משרות לחיפוש", "18–19.11"),
    ("דרושים", "3 עמודים לשאילתה", "5 עמודים לשאילתה", "19.11"),
    ("Indeed", "פעיל, 0 הגשות", "כבוי", "18.11"),
    ("מילות חיפוש", "17 · 5 בלי אף משרה טובה", "27 · \"⭐ מומלצות\" (11) · 5 מתות נמחקו", "18–19.11"),
    ("סינון רעש", "אין", "מילים לסינון · GTM תמיד נשאר · חסימת חברות", "19.11"),
    ("סייבר", "69 חברות · היו דליפות", "78 חברות + בדיקה שנייה באפליקציה", "19.11"),
    ("סף התאמה", "45", f"{MIN_FIT} · מתחת לסף לא מופיע לעולם", "19.11"),
    ("דשבורד", "עמוד אחד", "7 עמודים · טבלה · פעולות על כמה משרות · ביטול · Claude", "19.11"),
]


def optimization_block(s):
    """Assaf 19 Nov: the optimization report, live from the data (numbers update with every search)."""
    arc, jobs = s["archive"], s["jobs"]
    status = lambda j: (arc.get(j["key"]) or {}).get("status") or "open"
    scored = [j for j in jobs.values() if j.get("fit") is not None]
    good = [j for j in scored if j["fit"] >= MIN_FIT]
    sent = [j for j in jobs.values() if status(j) == "sent"]
    open_n = len(open_jobs(s)[0])
    claude = sum((r.get("claudeUsd") or 0) for r in s.get("runs", []))
    apify, apify_cap = apify_usage()
    total = claude + (apify or 0)
    st.markdown("##### 📈 דוח אופטימיזציה")
    st.caption("המספרים מתעדכנים אחרי כל חיפוש. עלות Apify היא של החודש הנוכחי, ועלות Claude היא מכל החיפושים השמורים.")
    k = st.columns(6)
    k[0].metric("משרות שנמצאו", len(jobs))
    k[1].metric(f"ציון {MIN_FIT}+", len(good), f"{_pct(len(good), len(scored))}% מהמדורגות", delta_color="off")
    k[2].metric("הגשות", len(sent))
    k[3].metric("עלות כוללת", f"${total:.1f}", f"Apify ${apify or 0:.1f} + Claude ${claude:.1f}", delta_color="off")
    k[4].metric("עלות להגשה", f"${total / max(1, len(sent)):.2f}")
    k[5].metric("משרות פתוחות", open_n, "התיבה נקייה" if not open_n else None, delta_color="off")

    t1, t2, t3, t4 = st.tabs(["🔁 לפני ← אחרי", "📊 הגשות וציונים", "📡 מקורות", "🧹 מה מסונן"])
    with t1:
        st.dataframe(pd.DataFrame([{"תחום": a, "לפני": b, "אחרי": c, "מתי": d} for a, b, c, d in OPT_LOG]), hide_index=True, width="stretch",
                     column_config={"אחרי": st.column_config.TextColumn("אחרי", width="large"), "לפני": st.column_config.TextColumn("לפני", width="medium")})
    with t2:
        c1, c2 = st.columns(2)
        days = {}
        for a in arc.values():
            if a.get("status") == "sent" and a.get("at"):
                d = to_il(a["at"]).date()
                days[d] = days.get(d, 0) + 1
        if days:
            first, today = min(days), dt.datetime.now(IL).date()
            rng = [first + dt.timedelta(days=i) for i in range((today - first).days + 1)]
            c1.caption(f"הגשות ביום (יעד {int(secret('DAILY_GOAL', 50))}) · ימים בלי עמודה = לא היו חיפושים")
            c1.bar_chart(pd.DataFrame({"הגשות": [days.get(d, 0) for d in rng]}, index=[d.strftime("%d.%m") for d in rng]), color=PAL["blue"], height=240)
        rows = []
        for lo, hi, name in [(MIN_FIT, 55, f"{MIN_FIT}–54"), (55, 65, "55–64"), (65, 75, "65–74"), (75, 101, "75+")]:
            b = [j for j in scored if lo <= j["fit"] < hi and status(j) in ("sent", "removed")]
            rows.append({"ציון": name, "% שנשלחו": _pct(sum(1 for j in b if status(j) == "sent"), len(b)), "משרות שסימנת": len(b)})
        c2.caption("אחוז הגשה לפי ציון: הציון מנבא מה תשלח")
        c2.bar_chart(pd.DataFrame(rows).set_index("ציון")[["% שנשלחו"]], color=PAL["green"], height=240)
    with t3:
        src = {}
        for j in jobs.values():
            for b in j.get("boards", []):
                o = src.setdefault(b, {"מקור": BOARD.get(b, b), "משרות": 0, f"{MIN_FIT}+": 0, "הגשות": 0})
                o["משרות"] += 1
                o[f"{MIN_FIT}+"] += (j.get("fit") or 0) >= MIN_FIT
                o["הגשות"] += status(j) == "sent"
        df = pd.DataFrame(sorted(src.values(), key=lambda o: -o["הגשות"]))
        if not df.empty:
            df["% נשלחו מ-" + f"{MIN_FIT}+"] = [_pct(a, b) for a, b in zip(df["הגשות"], df[f"{MIN_FIT}+"])]
            c1, c2 = st.columns(2)
            c1.caption("הגשות לפי מקור")
            c1.bar_chart(df.set_index("מקור")[["הגשות"]], horizontal=True, color=PAL["blue"], height=240)
            c2.caption(f"אחוז הגשה מתוך משרות {MIN_FIT}+ (גבוה = מקור מדויק)")
            c2.bar_chart(df.set_index("מקור")[["% נשלחו מ-" + f"{MIN_FIT}+"]], horizontal=True, color=PAL["green"], height=240)
    with t4:
        reasons = {"ציון מתחת לסף": 0, "מילים / חברות חסומות": 0, "סייבר": 0, "חו״ל: לא אנגלית / לא מרחוק / שפה נוספת": 0}
        for a in arc.values():
            if a.get("status") == "auto":
                reasons["סייבר" if a.get("reason") == "cyber" else "מילים / חברות חסומות" if a.get("reason") == "excluded"
                        else "חו״ל: לא אנגלית / לא מרחוק / שפה נוספת" if a.get("reason") == "intl" else "ציון מתחת לסף"] += 1
        cfg = settings_of(s)
        c1, c2 = st.columns([1.3, 1])
        c1.caption("משרות שהוסרו אוטומטית, לפי סיבה")
        c1.bar_chart(pd.DataFrame({"משרות": list(reasons.values())}, index=list(reasons)), horizontal=True, color=PAL["blue"], height=200)
        c2.markdown(f"- **{len(cfg['exclude_words'])}** מילים לסינון\n- **{len(cfg['blocked_companies'])}** חברות חסומות\n"
                    f"- **{len((DICT.get('_exclude') or {}).get('companies', []))}** חברות סייבר במנוע\n- תמיד נשאר: **GTM**"
                    + (f", {', '.join(cfg['keep_words'])}" if [w for w in cfg["keep_words"] if w.upper() != "GTM"] else ""))
    good_rate = _pct(len(sent), len(good))
    need = int(secret("DAILY_GOAL", 50)) / max(0.01, good_rate / 100)
    st.markdown(f"<div style='background:var(--js-blue-l);border-right:5px solid var(--js-blue);border-radius:10px;padding:10px 14px;margin:8px 0 4px;color:#1d3f8f'>"
                f"🧭 <b>הצעד הבא:</b> אתה שולח ל-{good_rate}% מהמשרות הטובות, ולכן כדי להגיע ל-{int(secret('DAILY_GOAL', 50))} הגשות ביום צריך כ-{need:.0f} "
                f"משרות עם ציון {MIN_FIT}+ בכל יום. הדרך לשם: \"⭐ מומלצות\" פעמיים ביום, בלי ימים מדולגים.</div>", unsafe_allow_html=True)


def page_stats():
    s, _ = load_state()
    arc, jobs = s["archive"], s["jobs"]
    today = dt.datetime.now(IL).date()
    days = [today - dt.timedelta(days=i) for i in range(13, -1, -1)]
    per_day = {d: 0 for d in days}
    for a in arc.values():
        if a.get("status") == "sent" and a.get("at"):
            d = to_il(a["at"]).date()
            if d in per_day:
                per_day[d] += 1
    goal = int(secret("DAILY_GOAL", 50))
    scored = [j for j in jobs.values() if j.get("fit") is not None]
    g45 = sum(1 for j in scored if j["fit"] >= MIN_FIT)
    sent_all = sum(1 for a in arc.values() if a.get("status") == "sent")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("נשלחו היום", per_day[today], f"{_pct(per_day[today], goal)}% מהיעד ({goal})", delta_color="off")
    m2.metric("נשלחו ב-7 ימים", sum(v for d, v in per_day.items() if d > today - dt.timedelta(days=7)))
    m3.metric("ציון 40+ מכל המשרות", f"{_pct(g45, len(scored))}%", f"{g45} מתוך {len(scored)}", delta_color="off")
    m4.metric("נשלחו מתוך 40+", f"{_pct(sent_all, g45)}%", f"{sent_all} נשלחו", delta_color="off")
    usd, cap_usd = apify_usage()
    m5.metric("Apify החודש", f"${usd:.2f}" if usd is not None else "?", f"{_pct(usd or 0, cap_usd or 0)}% מתוך ${cap_usd:.0f}" if cap_usd else None, delta_color="off")

    insights_block(s)
    optimization_block(s)
    research_block(s)
    st.markdown("##### ✨ שאל את הנתונים")
    ask_data_panel()

    st.markdown("##### הגשות ביום (14 ימים)")
    st.bar_chart(pd.DataFrame({"נשלחו": list(per_day.values())}, index=[d.strftime("%d.%m") for d in days]), height=220)
    kw_rows, src_rows = stats_tables(s)
    st.markdown("##### לפי מילה")
    st.caption("% 40+ = מתוך המשרות שקיבלו ציון · % נשלחו מ-40+ = כמה מהמשרות הטובות שלחת בפועל · % מסך הנשלחות = החלק של המילה מכל ההגשות")
    st.dataframe(pd.DataFrame(kw_rows).sort_values("נשלחו", ascending=False), hide_index=True, width="stretch",
                 column_config={"% 40+": PCT("% 40+"), "% 70+": PCT("% 70+"), "% נשלחו מ-40+": PCT("% נשלחו מ-40+"), "% מסך הנשלחות": PCT("% מסך הנשלחות")})
    st.markdown("##### לפי מקור")
    st.dataframe(pd.DataFrame(src_rows), hide_index=True, width="stretch",
                 column_config={"% 40+": PCT("% 40+"), "% נשלחו מ-40+": PCT("% נשלחו מ-40+"), "% מסך הנשלחות": PCT("% מסך הנשלחות")})
    st.caption("עלות משוערת: כ-$0.25 לכל מילת חיפוש (Apify כ-$0.16 + Claude כ-$0.09).")


LEVELS = ["Basic", "Familiarity", "Intermediate", "Advanced", "Expert"]
LEVEL_HE = {"Basic": "בסיסי", "Familiarity": "היכרות", "Intermediate": "בינוני", "Advanced": "מתקדם", "Expert": "מומחה"}
TAILOR_MODEL = MODEL
ANALYZE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["ats_keywords", "must_haves", "gaps", "skills_to_confirm", "recommendations"],
    "properties": {
        "ats_keywords": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["keyword", "in_cv", "required_by_post"],
                         "properties": {"keyword": {"type": "string"}, "in_cv": {"type": "boolean"}, "required_by_post": {"type": "boolean"}}}},
        "must_haves": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["requirement", "coverage", "evidence_in_cv"],
                       "properties": {"requirement": {"type": "string"}, "coverage": {"type": "string", "enum": ["covered", "partial", "missing"]},
                                      "evidence_in_cv": {"type": "string"}}}},
        "gaps": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["type", "item", "suggestion"],
                 "properties": {"type": {"type": "string", "enum": ["keyword", "system", "skill", "experience", "wording", "format"]},
                                "item": {"type": "string"}, "suggestion": {"type": "string"}}}},
        "skills_to_confirm": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["skill", "why_relevant"],
                              "properties": {"skill": {"type": "string"}, "why_relevant": {"type": "string"}}}},
        "recommendations": {"type": "array", "items": {"type": "string"}},
    },
}
TAILOR_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["name_line", "headline", "summary", "experience", "skills", "other_sections", "changes"],
    "properties": {
        "name_line": {"type": "string"}, "headline": {"type": "string"}, "summary": {"type": "string"},
        "experience": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["role", "company", "dates", "bullets"],
                       "properties": {"role": {"type": "string"}, "company": {"type": "string"}, "dates": {"type": "string"},
                                      "bullets": {"type": "array", "items": {"type": "string"}}}}},
        "skills": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["name", "level"],
                   "properties": {"name": {"type": "string"}, "level": {"type": "string"}}}},
        "other_sections": {"type": "string"},
        "changes": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["section", "change", "reason"],
                    "properties": {"section": {"type": "string"}, "change": {"type": "string"}, "reason": {"type": "string"}}}},
    },
}
TAILOR_RULES = (
    "HARD RULES: Use ONLY facts written in the ORIGINAL CV or in CONFIRMED SKILLS. Never invent experience, roles, employers, dates, achievements, "
    "systems, numbers or percentages. Keep every employer, title and date exactly as in the original. You may reorder, rephrase, shorten, choose "
    "which facts to highlight and use the post's terms for things he really did. A confirmed skill is written with its level (e.g. 'Salesforce - "
    "Familiarity'). Never mention what he lacks or did not do (no gap disclosure). Write in the same language as the original CV. "
    "Do not promise ATS results.")


@st.cache_data(ttl=3600, show_spinner=False)
def cv_text(lang, name):
    import io as _io
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(_io.BytesIO(cv_bytes(lang, name))).pages).strip()


def _tailor_call(user, schema, effort="medium"):
    import anthropic
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"), max_retries=3)
    with client.beta.messages.stream(model=TAILOR_MODEL, max_tokens=32000, messages=[{"role": "user", "content": user}],
                                     output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                                     betas=["server-side-fallback-2026-07-01"], fallbacks="default") as stream:
        r = stream.get_final_message()
    track(r)
    if r.stop_reason == "refusal":
        raise RuntimeError("Claude declined")
    return _json_reply(r)


def _job_block(j):
    return (f"JOB TITLE: {j.get('title')}\nCOMPANY: {j.get('company')}\nLOCATION: {j.get('location')}\n"
            f"JOB DESCRIPTION:\n{j.get('desc') or '(full text not saved; use the title, company and these requirements)'}\n"
            f"REQUIREMENTS FROM THE EARLIER REVIEW: {json.dumps([q.get('requirement') for q in (j.get('reqs') or [])], ensure_ascii=False)}")


SCAN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["best", "ranking"], "properties": {
    "best": {"type": "string"},
    "ranking": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["cv_file", "match", "level", "reason"], "properties": {
        "cv_file": {"type": "string"}, "match": {"type": "integer"}, "level": {"type": "string", "enum": ["none", "light", "moderate", "heavy"]},
        "reason": {"type": "string"}}}}}}
SCAN_LEVEL_HE = {"none": "ללא התאמה, מוכן להגשה", "light": "התאמה קלה", "moderate": "התאמה בינונית", "heavy": "התאמה משמעותית"}


def cv_scan(j, lang):
    """Assaf 22 Nov: before tailoring, read EVERY CV in the language folder and say which one fits this job best and how much tailoring it needs.
    One cheap call: the job + the first part of every CV. Stored with the job, so each job is scanned once."""
    import anthropic
    files = cv_files(lang) or cv_files("en")
    use = lang if cv_files(lang) else "en"
    files = [f for f in files if not re.search(r"cover|customer.?support|appointment|admin", f, re.I)]
    parts = [brain_block(use, files), ""]  # Assaf 24 Nov: the CV brain (profile + rules per CV) first, then the opening of each CV
    for f in files:
        try:
            parts.append(f"=== CV FILE: {f}\n{cv_text(use, f)[:1400]}")
        except Exception:
            continue
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"), max_retries=3)
    r = client.messages.create(
        model=MODEL, max_tokens=14000, output_config={"format": {"type": "json_schema", "schema": SCAN_SCHEMA}},
        messages=[{"role": "user", "content": (
            _job_block(j) + "\n\nBelow are ALL of the candidate's CVs. For each CV give match (0-100) = how well it already fits THIS job, and level = how much tailoring it needs: "
            "none (80+, submit as is), light (65-79, keyword and wording tweaks), moderate (45-64, reorder and rewrite parts), heavy (under 45, a major rewrite; "
            "usually another CV is a better base). reason = at most 12 Hebrew words. best = the cv_file name to start from. Rank all CVs, best first. "
            "Use only what is written in each CV.\n\n" + "\n\n".join(parts))}])
    track(r)
    d = _json_reply(r)
    d["ranking"] = sorted([x for x in d["ranking"] if x["cv_file"] in files], key=lambda x: -x["match"])
    if d["best"] not in files and d["ranking"]:
        d["best"] = d["ranking"][0]["cv_file"]
    d["at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return d


def tailor_log_add(j, cv, text, how):
    """Assaf 22 Nov: every tailored version is kept in the history (newest first, last 100)."""
    entry = {"id": uuid.uuid4().hex[:10], "key": j["key"], "title": j.get("title"), "company": j.get("company"), "cv": cv, "how": how,
             "lang": "he" if re.search(r"[\u0590-\u05ff]", text) else "en", "at": dt.datetime.now(dt.timezone.utc).isoformat(), "text": text}

    def m(st_):
        st_["tailor_log"] = [entry] + (st_.get("tailor_log") or [])[:99]
    update_state(m, f"tailor log: {j['key'][:30]}")


def tailor_history(s):
    log = s.get("tailor_log") or []
    with st.expander(f"📚 היסטוריית התאמות ({len(log)})"):
        if not log:
            st.caption("עוד אין התאמות. כל גרסה מותאמת שנוצרת נשמרת כאן אוטומטית.")
            return
        q = st.text_input("חיפוש", placeholder="🔎 חברה או תפקיד…", key="tl_q", label_visibility="collapsed")
        rows = [e for e in log if not q.strip() or q.strip().lower() in f"{e.get('title')} {e.get('company')} {e.get('cv')}".lower()]
        df = pd.DataFrame([{"תאריך": to_il(e["at"]).strftime("%d.%m %H:%M"), "משרה": e.get("title") or "", "חברה": e.get("company") or "",
                            "קורות חיים בסיס": str(e.get("cv", "")).replace(".pdf", ""), "שפה": "עב" if e.get("lang") == "he" else "EN",
                            "סוג": {"generated": "נוצר", "saved": "נשמר אחרי עריכה"}.get(e.get("how"), e.get("how"))} for e in rows])
        ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="tl_tbl", height=min(38 + 35 * len(rows), 330))
        if not (ev and ev.selection and ev.selection.rows):
            st.caption("בחר שורה כדי לראות את הגרסה ולהוריד אותה.")
            return
        e = rows[ev.selection.rows[0]]
        st.text_area("הטקסט", value=e["text"], height=260, key=f"tl_txt_{e['id']}")
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", f"{str(e.get('cv', '')).replace('.pdf', '')}-{e.get('company') or 'job'}")[:80]
        d1, d2, d3, d4, d5 = st.columns(5)
        try:
            from cv_pdf import cv_pdf
            d1.download_button("⬇ PDF", cv_pdf(e["text"], profile()), file_name=f"{stem}.pdf", mime="application/pdf", key=f"tl_pdf_{e['id']}", width="stretch", type="primary")
        except Exception as ex:
            d1.caption(f"PDF: {str(ex)[:60]}")
        d2.download_button("⬇ Word", docx_bytes(e["text"]), file_name=f"{stem}.docx", key=f"tl_doc_{e['id']}", width="stretch",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        d3.download_button("⬇ Markdown", e["text"].encode("utf-8"), file_name=f"{stem}.md", key=f"tl_md_{e['id']}", width="stretch")
        if d4.button("↗ פתח את המשרה", key=f"tl_job_{e['id']}", width="stretch", disabled=e["key"] not in s["jobs"]):
            st.session_state.tailor_job = e["key"]
            st.session_state.pop("tailor_pick", None)
            st.rerun()
        if d5.button("🗑 מחק", key=f"tl_del_{e['id']}", width="stretch"):
            update_state(lambda st_: st_.__setitem__("tailor_log", [x for x in (st_.get("tailor_log") or []) if x["id"] != e["id"]]), "tailor log delete")
            st.rerun()


def tailored_md(t):
    out = [f"# {t['name_line']}", f"**{t['headline']}**", "", "## Summary" if not re.search(r"[֐-׿]", t["summary"]) else "## תקציר", t["summary"], ""]
    out.append("## Experience" if not re.search(r"[֐-׿]", t["summary"]) else "## ניסיון")
    for e in t["experience"]:
        out += [f"### {e['role']} · {e['company']} · {e['dates']}"] + [f"- {b}" for b in e["bullets"]] + [""]
    if t["skills"]:
        out += ["## Skills" if not re.search(r"[֐-׿]", t["summary"]) else "## כישורים",
                " · ".join(f"{x['name']} ({x['level']})" if x.get("level") else x["name"] for x in t["skills"]), ""]
    if t["other_sections"].strip():
        out += [t["other_sections"].strip()]
    return "\n".join(out).strip()


def new_numbers(orig, new):
    """Guard against invented numbers: every number in the new version must appear in the original."""
    nums = lambda x: set(re.findall(r"\d[\d,.]*\+?%?", x))
    return sorted(n for n in nums(new) - nums(orig) if not re.fullmatch(r"(19|20)\d\d", n.strip(".,")))


def kw_cover(text, kws):
    t = text.lower()
    hit = [k for k in kws if k.lower() in t]
    return hit, (round(100 * len(hit) / len(kws)) if kws else 0)


def docx_bytes(md):
    import io as _io
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    d = Document()
    rtl = bool(re.search(r"[֐-׿]", md))
    for line in md.splitlines():
        if line.startswith("### "):
            p = d.add_heading(line[4:], level=3)
        elif line.startswith("## "):
            p = d.add_heading(line[3:], level=2)
        elif line.startswith("# "):
            p = d.add_heading(line[2:], level=1)
        elif line.startswith("- "):
            p = d.add_paragraph(line[2:], style="List Bullet")
        elif line.strip():
            p = d.add_paragraph(line.replace("**", ""))
        else:
            continue
        if rtl:
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    buf = _io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def diff_html(a, b):
    import difflib
    import html as _h
    out = []
    for op in difflib.ndiff(a.splitlines(), b.splitlines()):
        tag, line = op[:2], _h.escape(op[2:])
        if tag == "+ ":
            out.append(f"<div style='background:#E1F2E8;padding:1px 6px'>+ {line}</div>")
        elif tag == "- ":
            out.append(f"<div style='background:#f3f4f6;color:#6b7280;text-decoration:line-through;padding:1px 6px'>− {line}</div>")
        elif tag == "  " and line.strip():
            out.append(f"<div style='padding:1px 6px;color:#374151'>{line}</div>")
    return "<div style='font-size:.85rem;max-height:520px;overflow:auto;border:1px solid #e5e7eb;border-radius:8px'>" + "".join(out) + "</div>"


def page_tailor():
    """Assaf 21 Nov: tailor a CV to one job. The original PDF is never changed; the new version is downloaded as Word / Markdown."""
    s, _ = load_state()
    cand = sorted([j for j in s["jobs"].values() if (s["archive"].get(j["key"]) or {}).get("status") in (None, "sent")],
                  key=lambda j: (-(j.get("fit") or 0)))
    if not cand:
        st.info("אין משרות פתוחות. התאמת קו״ח פועלת על משרה מהתיבה.")
        return
    tailor_history(s)
    keys = [j["key"] for j in cand]
    pre = st.session_state.get("tailor_job")
    k = st.selectbox("משרה", keys, index=keys.index(pre) if pre in keys else 0,
                     format_func=lambda x: f"{s['jobs'][x].get('fit', '—')} · {s['jobs'][x].get('title')} · {s['jobs'][x].get('company')}", key="tailor_pick")
    j = s["jobs"][k]
    lang = j.get("lang", "en")
    files = cv_files(lang) or cv_files("en")
    scan = j.get("cvscan")
    if not scan or st.session_state.pop(f"rescan_{k}", False):
        with st.spinner("סורק את כל קורות החיים מול המשרה… (כ-20 שניות)"):
            try:
                scan = cv_scan(j, lang)
                update_state(lambda st_: st_["jobs"].get(k, {}).__setitem__("cvscan", scan), f"cv scan: {k}")
                st.session_state.pop(f"tailor_cv_{k}", None)
            except Exception as e:
                st.info(f"סריקת קורות החיים נכשלה: {_redact(e)[:150]}", icon="⚠️")
                scan = None
    if scan and scan.get("best") in files:
        st.session_state.setdefault(f"tailor_cv_{k}", scan["best"])
        top = scan["ranking"][0]
        with st.container(border=True):
            st.markdown(f"**המלצה:** להתחיל מ-**{scan['best'].replace('.pdf', '')}** · התאמה {top['match']} · {SCAN_LEVEL_HE.get(top['level'], top['level'])}  \n{top['reason']}")
            with st.expander("כל קורות החיים שנסרקו"):
                st.dataframe(pd.DataFrame([{"קורות חיים": x["cv_file"].replace(".pdf", ""), "התאמה": x["match"], "רמת התאמה נדרשת": SCAN_LEVEL_HE.get(x["level"], x["level"]),
                                            "למה": x["reason"]} for x in scan["ranking"]]), hide_index=True, width="stretch",
                             column_config={"התאמה": st.column_config.ProgressColumn("התאמה", min_value=0, max_value=100, format="%d")})
            if st.button("↻ סרוק מחדש", key=f"rescan_btn_{k}"):
                st.session_state[f"rescan_{k}"] = True
                st.rerun()
    st.session_state.setdefault(f"tailor_cv_{k}", j["cv"] if j.get("cv") in files else files[0])
    base = st.selectbox("קורות החיים המקוריים", files, key=f"tailor_cv_{k}")

    try:
        orig = cv_text(lang if base in cv_files(lang) else "en", base)
    except Exception as e:
        st.info(f"לא הצלחתי לקרוא את הקובץ: {_redact(e)[:150]}", icon="⚠️")
        return
    a, b = st.columns(2)
    a.download_button("⬇ הורד את המקור", cv_bytes(lang if base in cv_files(lang) else "en", base), file_name=base, mime="application/pdf", width="stretch")
    b.link_button("פתח את המשרה ↗", j.get("url") or "#", width="stretch")
    if not j.get("desc"):
        st.caption("תיאור המשרה המלא לא נשמר למשרה הזאת. הניתוח יתבסס על הכותרת ועל הדרישות שזוהו בדירוג.")
    S = st.session_state.setdefault("tailor", {}).setdefault(f"{k}|{base}", {})

    st.markdown("##### 1 · ניתוח פערים")
    if st.button("🔍 נתח את קורות החיים מול המשרה", type="primary", key="t_an"):
        with st.spinner("Claude מנתח… (כחצי דקה)"):
            try:
                S["an"] = _tailor_call(_job_block(j) + "\n\nORIGINAL CV:\n" + orig + "\n\nAnalyse this CV against this job for the candidate (answer texts in Hebrew, "
                                       "keywords as written in the post). ats_keywords: 10-25 terms from the post (systems, skills, role terms) and whether the CV "
                                       "has each. must_haves: the post's required items and how the CV covers them. gaps: missing keywords, systems not "
                                       "highlighted, unclear abilities, experience to bring forward, wording, format problems for ATS. skills_to_confirm: "
                                       "tools/skills the post wants that the CV does not show - he must confirm a level before any is added. "
                                       "recommendations: 3-6 concrete edits. " + TAILOR_RULES, ANALYZE_SCHEMA)
                S.pop("t", None)
            except Exception as e:
                st.info(f"הניתוח נכשל: {_redact(e)[:200]}", icon="⚠️")
    an = S.get("an")
    if not an:
        st.caption("כ-$0.05–0.10 לניתוח. המקור לא משתנה.")
        return
    kws = [x["keyword"] for x in an["ats_keywords"]]
    hit, pct = kw_cover(orig, kws)
    m1, m2, m3 = st.columns(3)
    m1.metric("מילות מפתח מהמשרה במקור", f"{pct}%", f"{len(hit)}/{len(kws)}", delta_color="off")
    mh = an["must_haves"]
    m2.metric("דרישות חובה מכוסות", f"{sum(1 for x in mh if x['coverage'] == 'covered')}/{len(mh)}")
    m3.metric("פערים שזוהו", len(an["gaps"]))
    with st.expander("פירוט: דרישות חובה, פערים והמלצות", expanded=True):
        ic = {"covered": "✅", "partial": "🟡", "missing": "❌"}
        st.markdown("\n".join(f"- {ic[x['coverage']]} **{x['requirement']}** · {x['evidence_in_cv']}" for x in mh))
        st.markdown("**פערים**\n" + "\n".join(f"- `{x['type']}` **{x['item']}** · {x['suggestion']}" for x in an["gaps"]))
        st.markdown("**המלצות**\n" + "\n".join(f"- {r}" for r in an["recommendations"]))
        st.markdown("**מילות מפתח:** " + " ".join(f"<span class='tag {'t-ok' if x['in_cv'] else 't-warn'}'>{E(str(x['keyword']))}</span>" for x in an["ats_keywords"]),
                    unsafe_allow_html=True)

    st.markdown("##### 2 · אישור מיומנויות")
    saved = settings_of(s).get("skill_levels") or {}
    levels = {}
    if an["skills_to_confirm"]:
        st.caption("מיומנות נכנסת לגרסה המותאמת רק אם אישרת רמה אמיתית. ברירת המחדל: לא להוסיף.")
        for x in an["skills_to_confirm"]:
            opts = ["לא להוסיף"] + LEVELS
            cur = saved.get(x["skill"])
            levels[x["skill"]] = st.selectbox(f"{x['skill']} · {x['why_relevant']}", opts, index=opts.index(cur) if cur in opts else 0,
                                              format_func=lambda o: LEVEL_HE.get(o, o) + (f" ({o})" if o in LEVEL_HE else ""), key=f"lvl_{k}_{x['skill']}")
    else:
        st.caption("אין מיומנויות שדורשות אישור.")
    confirmed = {sk: lv for sk, lv in {**saved, **levels}.items() if lv in LEVELS}

    st.markdown("##### 3 · גרסה מותאמת")
    if st.button("✍️ צור גרסה מותאמת", type="primary", key="t_gen"):
        if levels:
            new_saved = {**saved, **{sk: lv for sk, lv in levels.items() if lv in LEVELS}}
            if new_saved != saved:
                update_state(lambda st_: settings_of(st_).__setitem__("skill_levels", new_saved), "skill levels")
        with st.spinner("Claude כותב את הגרסה… (כדקה)"):
            try:
                t = _tailor_call(_job_block(j) + "\n\nORIGINAL CV:\n" + orig + f"\n\nCONFIRMED SKILLS (name: level): {json.dumps(confirmed, ensure_ascii=False)}"
                                 + "\n\nGAP ANALYSIS:\n" + json.dumps(an, ensure_ascii=False)
                                 + "\n\nWrite a tailored version of this CV for this job: headline and summary aimed at the post, every role kept with its "
                                   "employer/title/dates, bullets re-ordered and rephrased in the post's terms using only true facts, skills list (with levels for "
                                   "confirmed skills), other sections (education, languages) copied as they are. changes: what you changed and why (Hebrew). "
                                 + TAILOR_RULES, TAILOR_SCHEMA, effort="high")
                S["t"] = t
                S["md"] = tailored_md(t)
                st.session_state[f"t_edit_{k}|{base}"] = S["md"]
                try:
                    tailor_log_add(j, base, S["md"], "generated")
                except Exception:
                    pass
            except Exception as e:
                st.info(f"היצירה נכשלה: {_redact(e)[:200]}", icon="⚠️")
    if not S.get("t"):
        st.caption("כ-$0.10–0.20 לגרסה.")
        return
    t = S["t"]
    warn = new_numbers(orig, S["md"])
    if warn:
        st.info("מספרים שמופיעים בגרסה ולא במקור, צריך לבדוק או למחוק: " + ", ".join(warn), icon="⚠️")
    t1, t2, t3, t4 = st.tabs(["✏️ עריכה", "🔀 שינויים מול המקור", "📊 בדיקת ATS", "📝 מה שונה ולמה"])
    with t1:
        edited = st.text_area("הגרסה המותאמת (אפשר לערוך)", key=f"t_edit_{k}|{base}", height=520)
        e0, e1, e2, e3 = st.columns(4)
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", f"{base.replace('.pdf', '')}-{j.get('company') or 'job'}")[:80]
        try:  # Assaf 22 Nov: PDF in his CV design (Assaf-GTM-AI-Dev-v2) is the default download
            from cv_pdf import cv_pdf
            _he = bool(re.search(r"[֐-׿]", edited))
            e0.download_button("⬇ PDF (העיצוב שלי)", cv_pdf(edited, profile()), file_name=f"{stem}.pdf", width="stretch", type="primary", mime="application/pdf",
                               on_click=_save_resume, args=(f"{stem}.pdf", edited, "he" if _he else "en"))
            e0.caption("עותק נשמר גם בתיקיית Resume של " + ("עברית" if _he else "אנגלית") + " (דרך Sync-Cover-Letters.bat)")
        except Exception as e:
            e0.caption(f"ה-PDF נכשל: {_redact(e)[:80]}")
        e1.download_button("⬇ Word", docx_bytes(edited), file_name=f"{stem}.docx", width="stretch",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        e2.download_button("⬇ Markdown", edited.encode("utf-8"), file_name=f"{stem}.md", width="stretch")
        if e3.button("💾 שמור לאפליקציה", width="stretch", help="נשמר עם המשרה, כדי לחזור אליו אחר כך"):
            update_state(lambda st_: st_.setdefault("tailored", {}).__setitem__(k, {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "cv": base, "text": edited}),
                         f"tailored cv: {k}")
            tailor_log_add(j, base, edited, "saved")
            st.success("נשמר")
    with t2:
        st.caption("ירוק = נוסף או נוסח מחדש · אפור מחוק = הוסר מהמקור")
        st.markdown(diff_html(orig, st.session_state.get(f"t_edit_{k}|{base}", S["md"])), unsafe_allow_html=True)
    with t3:
        cur = st.session_state.get(f"t_edit_{k}|{base}", S["md"])
        hit2, pct2 = kw_cover(cur, kws)
        req_kws = [x["keyword"] for x in an["ats_keywords"] if x["required_by_post"]]
        r1, r2 = kw_cover(orig, req_kws)[1], kw_cover(cur, req_kws)[1]
        c1, c2, c3 = st.columns(3)
        c1.metric("כיסוי מילות מפתח", f"{pct2}%", f"{pct2 - pct:+d} מול המקור")
        c2.metric("כיסוי מילים מהדרישות", f"{r2}%", f"{r2 - r1:+d} מול המקור")
        c3.metric("מספרים לבדיקה", len(new_numbers(orig, cur)))
        miss = [x for x in kws if x not in hit2]
        st.markdown("**עדיין חסרות:** " + (", ".join(miss) if miss else "אין"))
        st.caption("בדיקת כיסוי וקריאות בלבד. אין הבטחה שמערכת ATS כלשהי תעביר את קורות החיים.")
    with t4:
        st.markdown("\n".join(f"- **{c['section']}**: {c['change']} · {c['reason']}" for c in t["changes"]))


def _out_save(k, patch):
    def m(s):
        s.setdefault("outreach", {}).setdefault(k, {}).update(patch)
    update_state(m, f"outreach {k[:30]}")


def _out_draft(j, contact):
    import anthropic
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"), max_retries=3)
    schema = {"type": "object", "additionalProperties": False, "required": ["subject", "email", "connect_note"],
              "properties": {"subject": {"type": "string"}, "email": {"type": "string"}, "connect_note": {"type": "string"}}}
    r = client.messages.create(
        model=MODEL, max_tokens=8000,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": (
            f"Write a short cold outreach from {PF.persona(profile())}, "
            f"to {contact or 'the hiring contact'} at {j.get('company')} about the open role '{j.get('title')}'.\n"
            f"Job description (excerpt): {(j.get('desc') or '')[:1500]}\n\n"
            "Rules: English, human and plain, 60-90 words, no links, no mention of a CV or attachment, never mention what the sender lacks or has not done, "
            "no invented numbers or claims, no flattery, no 'I hope this finds you well'. Say he saw the role, one concrete line on why he fits "
            "from the description, and offer to share more if relevant. Sign '" + PF.first_name(profile()) + "'. connect_note: LinkedIn connection note, max 280 characters, same tone.")}])
    track(r)
    d = _json_reply(r)
    d["connect_note"] = d["connect_note"][:290]
    return d


def page_outreach():
    st.markdown("### 👤 פנייה ישירה")
    try:
        _page_outreach()
    except Exception as e:
        if type(e).__name__ in ("RerunException", "StopException"):
            raise
        st.error(f"שגיאה בעמוד: {type(e).__name__}: {_redact(e)[:300]}")


def _page_outreach():
    """Assaf 21 Nov: find the poster, find + verify the email, review the drafts. Nothing is sent from here: the email opens in
    his own Gmail compose window, the LinkedIn note is copied by hand. Paid steps run only on a click."""
    import outreach as OUT
    s, _ = load_state()
    cand = [j for j in s["jobs"].values() if (s["archive"].get(j["key"]) or {}).get("status") in (None, "sent", "auto")]
    cand.sort(key=lambda j: -(j.get("fit") or 0))
    if not cand:
        st.info("אין משרות.")
        return
    keys = [j["key"] for j in cand]
    pre = st.session_state.get("outreach_job")
    k = st.selectbox("משרה", keys, index=keys.index(pre) if pre in keys else 0,
                     format_func=lambda x: f"{s['jobs'][x].get('fit', '—')} · {s['jobs'][x].get('title')} · {s['jobs'][x].get('company')}", key="out_pick")
    j = s["jobs"][k]
    O = {**(s.get("outreach", {}).get(k) or {}), **st.session_state.setdefault("out", {}).get(k, {})}

    def put(**kw):
        st.session_state["out"].setdefault(k, {}).update(kw)
        O.update(kw)
        _out_save(k, kw)  # 26 Nov: persist at once (found emails, verification and drafts were lost on refresh)

    st.link_button("פתח את המשרה ב-LinkedIn ↗", j.get("url") or "#")
    st.markdown("##### 1 · איש קשר")
    c1, c2 = st.columns(2)
    name = c1.text_input("שם", value=O.get("name") or j.get("poster") or "", key=f"on-{k}",
                         help="מפרסם המשרה כפי ש-LinkedIn החזיר. ריק = אין מפרסם, אפשר לחפש איש גיוס.")
    domain = c2.text_input("דומיין החברה", value=O.get("domain") or OUT.guess_domain(j.get("company")), key=f"od-{k}")
    sp_key = secret("SMARTLEAD_API_KEY")
    if not name and not sp_key:
        st.caption("חיפוש אנשי גיוס כבוי: חסר SMARTLEAD_API_KEY ב-Secrets. אפשר להקליד שם ידנית.")
    if not name and st.button("🔎 חפש אנשי גיוס בחברה (SmartProspect, חינם)", key=f"osp-{k}", disabled=not sp_key):
        try:
            res = OUT.sp_search(sp_key, j.get("company") or "", domain)
            put(people=res["people"][:8], filter_id=res["filter_id"])
        except Exception as e:
            st.warning(f"החיפוש נכשל: {_redact(e)[:150]}")
    if O.get("people") and not name:
        pick = st.radio("נמצאו", [f"{p['name']} · {p['title']}" for p in O["people"]], key=f"opk-{k}")
        st.caption("בחר ולחץ 'השתמש'. הכתובת לא נחשפת עד שתלחץ על חיפוש המייל.")
        if st.button("השתמש באיש הקשר הזה", key=f"ouse-{k}"):
            put(name=pick.split(" · ")[0])
            st.session_state.pop(f"on-{k}", None)  # let the text box take the new value
            st.rerun()
    if not name:
        st.info("כדי להמשיך למייל ולטיוטה: הקלד שם של איש קשר, או חפש אנשי גיוס בחברה.")
        return

    st.markdown("##### 2 · מייל ואימות")
    email = st.text_input("מייל", value=O.get("email") or "", key=f"oe-{k}", help="אפשר להדביק מייל שמצאת ידנית ולאמת אותו.")
    mv_key = secret("MILLIONVERIFIER_API_KEY")
    st.caption("סדר החיפוש: SmartProspect (קרדיט אחד) ← Apollo החינמי ← Actor של Apify (כ-$0.005). האימות רק ב-MillionVerifier.")
    f1, f2 = st.columns(2)
    if f1.button("🔍 מצא מייל", key=f"ofind-{k}", type="primary", width="stretch"):
        found, src, log = None, None, []
        steps = [("SmartProspect", sp_key, lambda: _sp_find(OUT, sp_key, j, name, domain, O)),
                 ("Apollo", secret("APOLLO_API_KEY"), lambda: OUT.apollo_match(secret("APOLLO_API_KEY"), name, domain)),
                 ("Apify", secret("APIFY_API_TOKEN"), lambda: OUT.apify_find(secret("APIFY_API_TOKEN"), name, domain))]
        with st.spinner("מחפש מייל…"):
            for label, key_, fn in steps:
                if not key_:
                    log.append(f"{label}: אין מפתח")
                    continue
                try:
                    found = fn()
                except Exception as e:
                    log.append(f"{label}: {_redact(e)[:100]}")
                    continue
                log.append(f"{label}: {'נמצא' if found else 'לא נמצא'}")
                if found:
                    src = label
                    break
        put(name=name, domain=domain, email=found or "", source=src or "", find_log=log, verified=None)
        st.session_state.pop(f"oe-{k}", None)
        st.rerun()
    if O.get("find_log"):
        st.caption(" · ".join(O["find_log"]))
    if f2.button("✅ אמת ב-MillionVerifier", key=f"over-{k}", width="stretch", disabled=not email):
        if not mv_key:
            st.warning("חסר MILLIONVERIFIER_API_KEY ב-Secrets")
        else:
            try:
                put(email=email, verified=OUT.mv_verify(mv_key, email))
                st.rerun()
            except Exception as e:
                st.warning(f"האימות נכשל: {_redact(e)[:150]}")
    v = O.get("verified")
    if v:
        (st.success if v["ok"] else st.error)(f"MillionVerifier: {v['result']}" + ("" if v["ok"] else " · לא נשלח למייל הזה"))

    st.markdown("##### 3 · טיוטות לאישור")
    if st.button("✍️ כתוב טיוטה", key=f"odr-{k}"):
        with st.spinner("Claude כותב…"):
            try:
                d = _out_draft(j, name)
                put(subject=d["subject"], body=d["email"], note=d["connect_note"])
                st.session_state.pop(f"osub-{k}", None), st.session_state.pop(f"obody-{k}", None), st.session_state.pop(f"onote-{k}", None)
                st.rerun()
            except Exception as e:
                st.warning(f"הכתיבה נכשלה: {_redact(e)[:150]}")
    if O.get("body"):
        sub = st.text_input("נושא", value=O["subject"], key=f"osub-{k}")
        body = st.text_area("גוף המייל", value=O["body"], height=190, key=f"obody-{k}")
        note = st.text_area("הערה לבקשת חיבור ב-LinkedIn", value=O.get("note", ""), height=80, max_chars=300, key=f"onote-{k}")
        st.caption(f"{len(note)}/300 תווים. בלי קו״ח ובלי קישור.")
        log_t = s.get("email_sent_log", [])
        ok_pace, msg = OUT.pacing(log_t)
        good = bool(email and v and v["ok"])
        a, b, c = st.columns(3)
        a.link_button("✉️ פתח ב-Gmail שלי", OUT.gmail_compose_url(email, sub, body), width="stretch", disabled=not (good and ok_pace),
                      type="primary", help="נפתח חלון כתיבה בחשבון ה-Gmail האישי שלך. אתה לוחץ שליחה.")
        if b.button("✓ שלחתי את המייל", key=f"osent-{k}", width="stretch", disabled=not (good and ok_pace)):
            def m(s_):
                s_.setdefault("email_sent_log", []).append(time.time())
                s_.setdefault("outreach", {}).setdefault(k, {}).update({"email": email, "name": name, "subject": sub, "body": body, "note": note, "emailed_at": time.time()})
            update_state(m, "outreach email sent")
            st.rerun()
        if c.button("💾 שמור טיוטה", key=f"osave-{k}", width="stretch"):
            _out_save(k, {"name": name, "domain": domain, "email": email, "subject": sub, "body": body, "note": note, "verified": v})
            st.toast("נשמר")
        if not good:
            st.caption("הכפתור נפתח אחרי שהמייל אומת כתקין.")
        elif not ok_pace:
            st.caption(msg)
        st.markdown("**בקשת חיבור:** העתק את ההערה ושלח ב-LinkedIn במקביל")
        st.code(note, language=None)
        st.link_button("חפש את " + name + " ב-LinkedIn ↗", "https://www.linkedin.com/search/results/people/?keywords=" + requests.utils.quote(f"{name} {j.get('company') or ''}"))
    if O.get("emailed_at"):
        st.caption("נשלח ב-" + to_il(dt.datetime.fromtimestamp(O["emailed_at"], dt.timezone.utc).isoformat()).strftime("%d.%m %H:%M"))


def _sp_find(OUT, key, j, name, domain, O):
    res = OUT.sp_search(key, j.get("company") or "", domain)
    fid = res.get("filter_id")
    if not fid or not any(p["name"].strip().lower() == name.strip().lower() for p in res["people"]):
        return None
    return OUT.sp_unlock(key, fid, name)


# ---------------- app shell ----------------
state, _ = load_state()
PAGES = {
    "inbox": st.Page(page_inbox, title="משרות", icon=":material/inbox:", url_path="inbox", default=True),
    "search": st.Page(page_search, title="חיפוש חדש", icon=":material/search:", url_path="search"),
    "stats": st.Page(page_stats, title="סטטיסטיקה", icon=":material/insights:", url_path="stats"),  # Assaf 19 Nov: above search history
    "tailor": st.Page(page_tailor, title="התאמת קו״ח", icon=":material/edit_document:", url_path="tailor"),
    "low": st.Page(page_low, title="מתחת לסף", icon=":material/arrow_downward:", url_path="low"),
    "outreach": st.Page(page_outreach, title="פנייה ישירה", icon=":material/forward_to_inbox:", url_path="outreach"),
    "history": st.Page(page_history, title="היסטוריית חיפושים", icon=":material/history:", url_path="history"),
    "companies": st.Page(page_companies, title="חברות", icon=":material/apartment:", url_path="companies"),
    "filters": st.Page(page_filters, title="סינון", icon=":material/block:", url_path="filters"),
    "archive": st.Page(page_archive, title="ארכיון", icon=":material/inventory_2:", url_path="archive"),
}
nav = st.navigation(list(PAGES.values()), position="sidebar")
with st.sidebar:
    st.markdown("<div class='brand'>Job Scout<small>חיפוש עבודה · ישראל</small></div>", unsafe_allow_html=True)
    goal = int(secret("DAILY_GOAL", 50))
    sent_n = sent_today(state)
    st.progress(min(1.0, sent_n / max(1, goal)), text=f"נשלחו היום **{sent_n}** / {goal}")
    open_n = len(open_jobs(state)[0])
    cap = 60
    flush_usage()
    _u = (state.get("usage") or {}).get(dt.datetime.now(IL).strftime("%Y-%m"), {})
    _p = SH.get("usage_pending") or {}
    _tin, _tout = _u.get("in", 0) + _p.get("in", 0), _u.get("out", 0) + _p.get("out", 0)
    usd, cap_usd = apify_usage()
    st.markdown(f"<div class='side-kpi'><span>משרות פתוחות</span><b>{open_n}</b></div>"
                f"<div class='side-kpi'><span>חיפושים היום</span><b>{runs_today(state)}/{cap}</b></div>"
                + f"<div class='side-kpi'><span>Claude החודש (טוקנים)</span><b>{(_tin + _tout) / 1e6:.2f}M</b></div>"
                f"<div class='side-kpi'><span>קלט / פלט</span><b>{_tin / 1e6:.2f}M / {_tout / 1e6:.2f}M</b></div>"
                + (f"<div class='side-kpi'><span>Apify החודש</span><b>${usd:.2f} מתוך ${cap_usd:.0f}</b></div>" if usd is not None else ""),
                unsafe_allow_html=True)
    _b, _info = active_batch()
    if _info and not _info["done"]:
        st.fragment(run_every=4)(progress_live)(compact=True)
    st.divider()
    if st.button("יציאה", width="stretch"):
        st.session_state.clear()
        st.rerun()
nav.run()
