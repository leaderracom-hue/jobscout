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
KEYWORDS = ["product ai", "marketing automation", "product marketing", "gtm product", "gtm ai", "gtm engineer", "ai builder", "ai automation",
            "automation", "crm integration", "crm automation", "ai trainer", "freelancer", "מנהל מוצר", "מנהל מוצר אוטומציות", "אוטומציות שיווק", "מנהל פרויקט אוטומציות",
            # Assaf 18 Nov: 12 more
            "מיישם פתרונות ai", "ai implementation", "ai solutions", "ai product owner", "technical product manager", "data product manager",
            "product operations", "crm product", "lifecycle marketing", "טרנספורמציה דיגיטלית", "מנהל חדשנות", "מנהל מערכות שיווק"]
LABEL = {"product ai": "Product AI", "marketing automation": "Marketing Automation", "product marketing": "Product Marketing",
         "gtm product": "GTM Product", "gtm ai": "GTM AI", "gtm engineer": "GTM Engineer", "ai builder": "AI Builder", "ai automation": "AI Automation", "automation": "Automation",
         "crm integration": "CRM Integration", "crm automation": "CRM Automation", "ai trainer": "AI Trainer", "freelancer": "Freelancer", "מנהל מוצר": "מנהל מוצר", "מנהל מוצר אוטומציות": "מנהל מוצר אוטומציות", "אוטומציות שיווק": "אוטומציות שיווק", "מנהל פרויקט אוטומציות": "מנהל פרויקט אוטומציות",
         "מיישם פתרונות ai": "מיישם פתרונות AI", "ai implementation": "AI Implementation", "ai solutions": "AI Solutions", "ai product owner": "AI Product Owner",
         "technical product manager": "Technical Product Manager", "data product manager": "Data Product Manager", "product operations": "Product Operations",
         "crm product": "CRM / Salesforce Product", "lifecycle marketing": "Lifecycle / Retention", "טרנספורמציה דיגיטלית": "טרנספורמציה דיגיטלית",
         "מנהל חדשנות": "מנהל חדשנות", "מנהל מערכות שיווק": "מנהל מערכות שיווק"}
WINDOWS = {24: "24 שעות", 48: "48 שעות", 72: "72 שעות", 0: "שבוע"}
MIN_FIT = 45  # Assaf 11 Nov: a score under 45 goes straight to the archive ("auto")
HISTORY_HOURS = 72  # Assaf: past searches can be reopened for 72 hours
BOARD = {"linkedin": "LinkedIn", "indeed": "Indeed", "drushim": "דרושים", "alljobs": "AllJobs", "jobmaster": "JobMaster", "careers": "דף קריירה"}

st.set_page_config(page_title="Job Scout", page_icon=":material/work:", layout="wide")
st.markdown("""<style>
.block-container{max-width:1150px;padding-top:1.6rem}
.stMainBlockContainer, .stMarkdown, .stCaption, [data-testid="stExpander"], [data-testid="stSidebarContent"]{direction:rtl;text-align:right}
[data-testid="stMetric"]{background:#f6f8fb;border:1px solid #e6eaf0;border-radius:12px;padding:10px 14px}
[data-testid="stMetricValue"]{font-size:1.6rem}
.brand{font-weight:700;font-size:1.35rem;margin:-6px 0 10px}.brand small{display:block;font-weight:400;font-size:.78rem;color:#6b7280}
.side-kpi{display:flex;justify-content:space-between;padding:5px 2px;border-bottom:1px solid #eef0f3;font-size:.88rem}.side-kpi b{font-weight:600}
.fit{width:54px;height:54px;border-radius:12px;display:flex;flex-direction:column;align-items:center;justify-content:center;
     font-family:ui-monospace,monospace;font-weight:700;font-size:1.25rem;line-height:1}
.fit small{font-size:.58rem;font-weight:500;margin-top:3px;font-family:sans-serif}
.f-hi{background:#e3f3ea;color:#16794a}.f-mid{background:#fbf0dc;color:#9a5b00}.f-lo{background:#fbe7e6;color:#b3322f}.f-na{background:#eef0f3;color:#5b6474}
.jt{font-weight:600;font-size:1.05rem;line-height:1.35}.jt a{color:inherit;text-decoration:none}.jt a:hover{color:#2a55c9}
.sub{color:#6b7280;font-size:.86rem;margin-top:1px}
.tags{margin-top:4px}
.tag{display:inline-block;font-size:.72rem;border:1px solid #dde1e8;border-radius:999px;padding:0 9px;margin:2px 0 0 4px;color:#5b6474;background:#fff}
.t-new{background:#2a55c9;border-color:#2a55c9;color:#fff}.t-kw{color:#7a3fb0;border-color:#d9c3f0;background:#faf6fe}
.t-ok{color:#16794a;border-color:#bfe3cd}.t-cv{color:#2a55c9;border-color:#c9d6f5;background:#f3f6fd;font-family:ui-monospace,monospace}
details.why{margin-top:5px;font-size:.88rem;color:#374151}details.why summary{cursor:pointer;color:#2a55c9;font-size:.8rem;list-style:none}
details.why summary::before{content:"▸ "}details.why[open] summary::before{content:"▾ "}
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
            st.error("סיסמה שגויה")
    st.stop()


gate()


# ---------------- private data repo ----------------
def gh_headers(raw=False):
    return {"Authorization": f"Bearer {secret('GH_DATA_TOKEN')}", "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def gh_url(path):
    return f"{GH}/repos/{secret('GH_DATA_REPO')}/contents/{path}"


def load_state():
    # GitHub may serve a read up to ~60 s old right after a write, so the copy this server last wrote wins for 90 s.
    c = SH["latest"]
    if c and time.time() - c[2] < 90:
        return json.loads(json.dumps(c[0])), c[1]
    r = requests.get(gh_url("state.json"), headers=gh_headers(), timeout=20)
    if r.status_code == 404:
        return {"jobs": {}, "archive": {}, "runs": []}, None
    r.raise_for_status()
    meta = r.json()
    raw = base64.b64decode(meta["content"]) if meta.get("content") else requests.get(gh_url("state.json"), headers=gh_headers(True), timeout=30).content
    return json.loads(raw), meta["sha"]


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
    """Read, apply the change, write back. One writer at a time on this server (lock); retry if GitHub saw another write."""
    with SH["lock"]:
        for _ in range(6):
            s, sha = load_state()
            mutate(s)
            body = {"message": message, "content": base64.b64encode(json.dumps(s, ensure_ascii=False, indent=1).encode()).decode()}
            if sha:
                body["sha"] = sha
            r = requests.put(gh_url("state.json"), headers=gh_headers(), json=body, timeout=30)
            if r.status_code in (200, 201):
                SH["latest"] = (s, r.json()["content"]["sha"], time.time())
                return s
            SH["latest"] = None
            if r.status_code not in (409, 422):
                r.raise_for_status()
            time.sleep(1.0)
    raise RuntimeError("could not save to the data repo")


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
BRAIN = (APP / "engine/config/match-brain.md").read_text(encoding="utf-8")


def norm(s):
    return re.sub(r"[^a-z֐-׿0-9]", "", str(s or "").lower())


def key_of(j):
    return norm(j.get("company"))[:10] + "|" + norm(j.get("title"))[:24]


def lang_of(j):
    s = f"{j.get('title', '')} {str(j.get('desc', ''))[:800]}"
    he, lat = len(re.findall(r"[֐-׿]", s)), len(re.findall(r"[A-Za-z]", s))
    return "he" if he > lat * 0.5 else "en"


def cv_pool(kw, lang):
    have = cv_files(lang)
    listed = [f for f in (DICT.get(kw, {}).get("cv", {}).get(lang) or []) if f in have]
    return listed or [f for f in have if not re.search(r"cover|customer.?support|appointment|admin", f, re.I)]


def score_job(j, kw):
    lang = lang_of(j)
    pool = cv_pool(kw, lang)
    user = "\n".join([
        f"TODAY'S DATE: {dt.date.today()}", f"TITLE: {j['title']}", f"COMPANY: {j.get('company') or 'not stated'}",
        f"LOCATION ON THE POST: {j.get('location') or 'Israel'}", "", "FULL DESCRIPTION:",
        str(j.get("desc") or "")[:9000] or "(the post has no description text; judge from the title and company)", "",
        "EXTRA FIELDS for this request, add them to the same JSON object:",
        f'"cv_file": the ONE file from this list that best fits this job, copied exactly: {json.dumps(pool, ensure_ascii=False)}',
        '"why_he": ONE short sentence in simple, natural, everyday Hebrew (max 18 words), speaking to Assaf in second person ("יש לך…", "חסר לך…"): '
        'the main reason it fits, then the main gap after "פער:". Keep tool and product names in English (HubSpot, Braze, n8n). No odd or invented '
        'Hebrew words; if unsure of a Hebrew word, use the plain English term. Male second person ("אתה", never "את"). NEVER state how many years or '
        'months he has (no "9 חודשים", "7+ שנים") — those numbers are not known; you may quote what the POST requires ("דורשים 3 שנים"). '
        "Hebrew letters, English and digits only.",
        "HARD RULE: if the employer is a cybersecurity / information-security company, or the role is in cyber or security, set \"fit\" to 0.",
    ])
    for attempt in range(1, 6):
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=90, headers={
            "x-api-key": secret("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5", "max_tokens": 1000, "system": BRAIN, "messages": [{"role": "user", "content": user}]})
        if (r.status_code == 429 or r.status_code >= 500) and attempt < 5:
            time.sleep(1.5 * attempt)
            continue
        r.raise_for_status()
        b = r.json()
        text = "".join(c.get("text", "") for c in b.get("content", []))
        o = json.loads(text[text.index("{"): text.rindex("}") + 1])
        u = b.get("usage", {})
        return {"fit": max(0, min(100, round(float(o.get("fit") or 0)))), "verdict": o.get("verdict", ""),
                "why": o.get("why_he") or o.get("one_line", ""), "gaps": o.get("gaps", []), "lang": lang,
                "cv": o.get("cv_file") if o.get("cv_file") in pool else (pool[0] if pool else ""),
                "usd": u.get("input_tokens", 0) / 1e6 + u.get("output_tokens", 0) / 1e6 * 5}


def runs_today(s):
    today = dt.datetime.now(IL).date().isoformat()
    return sum(1 for r in s.get("runs", []) if dt.datetime.fromisoformat(r["at"].replace("Z", "+00:00")).astimezone(IL).date().isoformat() == today)


def archived(s, j):
    if key_of(j) in s["archive"]:
        return True
    urls = set((j.get("links") or {}).values()) | {j.get("url")}
    return any(urls & set(a.get("urls") or []) for a in s["archive"].values())


def search_core(kw, hours, batch, report):
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
    test_free = ["--free"] if os.environ.get("JOBSCOUT_TEST_FREE") == "1" else []  # local test only: free sources, no Apify
    p = subprocess.run(["node", str(APP / "engine/mini-agent/scout.mjs"), kw, "--out", out, "--days", str(days), *test_free],
                       cwd=str(APP / "engine"), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if p.returncode != 0:
        report("error", error=(p.stdout + p.stderr)[-600:])
        return
    found = json.loads(Path(out, "results-full.json").read_text(encoding="utf-8"))
    stats = json.loads(Path(out, "results.json").read_text(encoding="utf-8")).get("stats", {})  # per source: pulled / dropped / kept
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
    report("scoring", found=len(found), new=len(fresh))
    scores, usd = {}, 0.0
    with ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(score_job, j, kw): k for k, j in fresh}
        for i, f in enumerate(as_completed(futs), 1):
            k = futs[f]
            try:
                sc = f.result()
                usd += sc.pop("usd")
                scores[k] = sc
            except Exception as e:
                scores[k] = {"scoreError": str(e)[:160]}
            report("scoring", scored=i)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=HISTORY_HOURS)).isoformat()

    autoc = {}

    def mutate(st_):
        for k, j in seen_again.items():
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
            fit = scores.get(k, {}).get("fit")
            if fit is not None and fit < MIN_FIT:  # never shown, never returns
                st_["archive"][k] = {"status": "auto", "fit": fit, "at": now, "title": j["title"], "company": j.get("company"),
                                     "urls": list((j.get("links") or {"x": j.get("url")}).values())}
        auto = sum(1 for k, _ in fresh if (scores.get(k, {}).get("fit") is not None and scores[k]["fit"] < MIN_FIT))
        autoc["n"] = auto
        runs =[{"kw": kw, "hours": hours or None, "at": now, "batch": batch, "found": len(found), "new": len(fresh), "autoRemoved": auto,
                 "stats": stats, "keys": keys, "apifyUsd": float(spent.group(1)) if spent else None, "claudeUsd": round(usd, 3)}] + st_.get("runs", [])[:199]
        for r in runs:  # history keeps the job list for 72 hours only
            if r.get("keys") and r["at"] < cutoff:
                r.pop("keys")
        st_["runs"] = runs

    update_state(mutate, f"run {kw} {hours or 'week'}h: {len(fresh)} new")
    report("done", new=len(fresh), auto=autoc.get("n", 0), usd=round(usd, 3))


def start_batch(kws, hours):
    """Start a multi-keyword search in the background (3 keywords at a time) and return its id."""
    batch = uuid.uuid4().hex[:10]
    info = {"kws": list(kws), "hours": hours, "started": time.time(), "kw": {k: {"phase": "queued"} for k in kws}, "done": False}
    SH["batches"][batch] = info

    def run_one(k):
        def report(phase, **extra):
            info["kw"][k] = {**info["kw"][k], "phase": phase, **extra}
        try:
            search_core(k, hours, batch, report)
        except Exception as e:
            report("error", error=str(e)[:300])

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
    "AI": ["ai builder", "ai automation", "מיישם פתרונות ai", "ai implementation", "ai solutions", "ai trainer"],
    "CRM": ["crm product", "crm integration", "crm automation"],
    "GTM ועוד": ["gtm product", "gtm ai", "gtm engineer", "טרנספורמציה דיגיטלית", "מנהל חדשנות", "freelancer"],
}
TOP10 = ["מנהל מוצר אוטומציות", "marketing automation", "ai builder", "אוטומציות שיווק", "gtm product", "product ai", "מנהל מוצר",
         "gtm engineer", "product marketing", "ai automation"]
NEW12 = ["מיישם פתרונות ai", "ai implementation", "ai solutions", "ai product owner", "technical product manager", "data product manager",
         "product operations", "crm product", "lifecycle marketing", "טרנספורמציה דיגיטלית", "מנהל חדשנות", "מנהל מערכות שיווק"]
USD_PER_KW = 0.25  # measured 18 Nov: Apify ~$0.16 + Claude ~$0.09 per keyword
AGE = {"all": "הכל", "1": "יום", "3": "3 ימים", "7": "שבוע", "older": "מעל שבוע"}
PHASE = {"queued": "ממתין", "pulling": "מושך משרות", "scoring": "מדרג", "done": "הסתיים", "error": "נכשל"}


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


def mark_many(keys, status):
    """Sent / removed for one or many jobs in ONE save; remembers the last action so it can be undone."""
    keys = list(keys)

    def m(s):
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for key in keys:
            j = s["jobs"].get(key, {})
            s["archive"][key] = {"status": status, "at": now, "title": j.get("title"), "company": j.get("company"),
                                 "urls": list((j.get("links") or {"x": j.get("url")}).values())}
    update_state(m, f"{status}: {keys[0]}" if len(keys) == 1 else f"{status} x{len(keys)}")
    st.session_state.undo = {"keys": keys, "status": status}
    for k in keys:
        st.session_state.pop(f"sel-{k}", None)


def restore_many(keys):
    keys = list(keys)

    def m(s):
        for key in keys:
            s["archive"].pop(key, None)
    update_state(m, f"restore x{len(keys)}")


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
        d = requests.get("https://api.apify.com/v2/users/me/limits", params={"token": secret("APIFY_API_TOKEN")}, timeout=10).json()["data"]
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
            extra = f"{v.get('new', 0)} חדשות" + (f" · {v.get('auto')} מתחת ל-{MIN_FIT}" if v.get("auto") else "")
        elif v.get("phase") == "error":
            extra = str(v.get("error", ""))[:80]
        rows.append({"": {"done": "✅", "error": "⚠️"}.get(v.get("phase"), "⏳"), "מילה": lbl(k), "שלב": PHASE.get(v.get("phase"), v.get("phase")), "פרטים": extra})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


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
    word = "סומנו כנשלחו" if u["status"] == "sent" else "הוסרו"
    a, b, c = st.columns([5, 1, 1], vertical_alignment="center")
    a.success(f"{len(u['keys'])} משרות {word}")
    if b.button("↩ בטל", key="undo_btn", width="stretch"):
        restore_many(u["keys"])
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


def fit_cls(fit):
    return "f-na" if fit is None else "f-hi" if fit >= 80 else "f-mid" if fit >= 60 else "f-lo"


def card(j, new=False):
    k = j["key"]
    fit = j.get("fit")
    cv = j.get("cv") or ""
    tags = '<span class="tag t-new">חדשה</span>' if new else ""
    tags += "".join(f'<span class="tag t-kw">{lbl(x)}</span>' for x in j.get("kws", []))
    tags += "".join(f'<span class="tag">{BOARD.get(b, b)}</span>' for b in j.get("boards", []))
    if j.get("easyApply"):
        tags += '<span class="tag t-ok">Easy Apply</span>'
    if cv:
        tags += f'<span class="tag t-cv">קו״ח {"עב" if j.get("lang") == "he" else "EN"}: {cv.replace(".pdf", "")}</span>'
    why = j.get("why") or (f"הדירוג נכשל: {j['scoreError']}" if j.get("scoreError") else "")
    gaps = j.get("gaps") or []
    more = ("<br>" + " · ".join(str(g) for g in gaps[:3])) if gaps else ""
    with st.container(border=True):
        c0, c1, c2 = st.columns([0.35, 0.9, 10], vertical_alignment="top")
        c0.checkbox("בחר", key=f"sel-{k}", label_visibility="collapsed")
        c1.markdown(f'<div class="fit {fit_cls(fit)}">{fit if fit is not None else "—"}<small>התאמה</small></div>', unsafe_allow_html=True)
        c2.markdown(
            f'<div class="jt"><a href="{j.get("url")}" target="_blank">{j.get("title")}</a></div>'
            f'<div class="sub">{j.get("company") or "—"} · {j.get("location") or "ישראל"} · {ago(j)}</div>'
            f'<div class="tags">{tags}</div>'
            + (f'<details class="why"><summary>למה זה מתאים</summary>{why}{more}</details>' if why else ""),
            unsafe_allow_html=True)
        a1, a2, a3, a4 = c2.columns([2, 1.3, 1.3, 1.3])
        a1.link_button("פתח והגש ↗", j.get("url") or "#", type="primary", width="stretch")
        if cv and st.session_state.get("cv_open") == k:
            try:
                a2.download_button("⬇ הורד", cv_bytes(j.get("lang", "en"), cv), file_name=cv, mime="application/pdf", key=f"d-{k}", width="stretch")
            except Exception:
                a2.caption("הקובץ לא נמצא")
        elif cv and a2.button("קו״ח", key=f"c-{k}", width="stretch"):
            st.session_state.cv_open = k
            st.rerun()
        if a3.button("✓ שלחתי", key=f"s-{k}", width="stretch"):
            mark_many([k], "sent")
            st.rerun()
        if a4.button("✕ הסר", key=f"x-{k}", width="stretch"):
            mark_many([k], "removed")
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


def inbox_body():
    s, _ = load_state()
    undo_bar()
    queue = s.get("queue", {})
    opn = [j for j in s["jobs"].values() if j["key"] not in s["archive"] and j["key"] not in queue]
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
        f1, f2, f3, f4 = st.columns([3, 2.2, 2, 1.6])
        q = f1.text_input("חיפוש", placeholder="🔎 חברה או תפקיד…", key="f_q", label_visibility="collapsed")
        kw_opts = sorted({k for j in opn for k in j.get("kws", [])}, key=lbl)
        f_kw = f2.multiselect("מילה", kw_opts, format_func=lbl, placeholder="כל המילים", key="f_kw", label_visibility="collapsed")
        src_opts = sorted({x for j in opn for x in j.get("boards", [])})
        f_src = f3.multiselect("מקור", src_opts, format_func=lambda x: BOARD.get(x, x), placeholder="כל המקורות", key="f_src", label_visibility="collapsed")
        f_min = f4.selectbox("ציון", [0, 60, 70, 80], format_func=lambda v: "כל הציונים" if not v else f"ציון {v}+", key="f_min", label_visibility="collapsed")
        g1, g2, g3, g4 = st.columns([3.2, 1.3, 1.8, 2])
        age = g1.segmented_control("פורסם", list(AGE), format_func=lambda a: AGE[a], default="all", key="age_view", label_visibility="collapsed") or "all"
        only_new = g2.toggle("רק חדשות", key="only_new")
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
    if only_new:
        shown = [j for j in shown if is_new(j)]
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
        return

    n_all = len(shown)
    p1, p2, p3, p4 = st.columns([1.6, 3, 1, 1], vertical_alignment="center")
    size = p1.segmented_control("בעמוד", [10, 25, 50], default=25, key="page_size", label_visibility="collapsed") or 25
    pages = max(1, -(-n_all // size))
    sig = (q, tuple(f_kw), tuple(f_src), f_min, age, only_new, sort, size, bool(hist))
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


def page_search():
    s, _ = load_state()
    cap = int(secret("MAX_SEARCHES_PER_DAY", 30))
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

    st.markdown("##### מילות חיפוש")
    p = st.columns(5)
    p[0].button("⭐ 10 החזקות", on_click=_preset, args=(TOP10,), width="stretch")
    p[1].button("🆕 12 החדשות", on_click=_preset, args=(NEW12,), width="stretch")
    p[2].button("כל המילים", on_click=_preset, args=(KEYWORDS,), width="stretch")
    p[3].button("נקה", on_click=_preset, args=([],), width="stretch")
    picked = []
    for i, (g, items) in enumerate(GROUPS.items()):
        if f"kwg_{i}" not in st.session_state:
            st.session_state[f"kwg_{i}"] = []
        picked += st.pills(g, items, format_func=lbl, selection_mode="multi", key=f"kwg_{i}") or []
    free = st.text_input("מילים נוספות (מופרדות בפסיק)", key="kw_free", placeholder="למשל: revops, מנהל CRM")
    kws = list(dict.fromkeys(picked + [w.strip().lower() for w in free.split(",") if w.strip()]))

    used = runs_today(s)
    pending = sum(1 for i in SH["batches"].values() if not i["done"] for v in i["kw"].values() if v.get("phase") not in ("done", "error"))
    left = cap - used - pending
    with st.container(border=True):
        w1, w2 = st.columns([3, 2], vertical_alignment="bottom")
        win = w1.segmented_control("משרות שפורסמו ב-", list(WINDOWS), format_func=lambda h: WINDOWS[h], default=48, key="win") or 0
        mins = -(-len(kws) // 3) * 3
        w2.markdown(f"**{len(kws)} מילים** · כ-${len(kws) * USD_PER_KW:.2f} · כ-{mins} דק'  \n"
                    f"<span class='sub'>היום {used + pending}/{cap} חיפושים · נשארו {max(0, left)}</span>", unsafe_allow_html=True)
        over = len(kws) > left
        if over:
            st.warning(f"בחרת {len(kws)} מילים, אבל נשארו היום רק {max(0, left)} חיפושים. הקטן את הבחירה או העלה את MAX_SEARCHES_PER_DAY ב-Secrets.")
        if st.button(f"🔍 חפש {len(kws)} מילים · {WINDOWS[win]}", type="primary", disabled=running or not kws or over, width="stretch"):
            st.session_state.batch = start_batch(kws, win)
            st.session_state.pop("hist", None)
            st.rerun()
        if running:
            st.caption("חיפוש רץ כבר. אפשר לעבור לתיבת המשרות; משרות נכנסות לשם בכל פעם שמילה מסתיימת.")


def page_history():
    s, _ = load_state()
    st.markdown("##### חיפושים אחרונים")
    st.caption(f"{HISTORY_HOURS} השעות האחרונות. \"פתח\" מציג בתיבה רק את המשרות של החיפוש הזה, בלי עלות.")
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=HISTORY_HOURS)).isoformat()
    groups = {}
    for r in s.get("runs", []):
        if r.get("keys") and r["at"] >= cutoff:
            groups.setdefault(r.get("batch") or r["at"], []).append(r)
    if not groups:
        st.info("אין חיפושים מ-72 השעות האחרונות.")
    for g, runs in sorted(groups.items(), key=lambda x: max(r["at"] for r in x[1]), reverse=True):
        keys = list(dict.fromkeys(k for r in runs for k in r["keys"]))
        open_n = sum(1 for k in keys if k not in s["archive"])
        at = to_il(min(r["at"] for r in runs)).strftime("%d.%m %H:%M")
        label = f"{at} · {len(runs)} מילים · {WINDOWS.get(runs[0].get('hours') or 0, 'שבוע')}"
        with st.container(border=True):
            a, c = st.columns([5, 1], vertical_alignment="center")
            a.markdown(f"**{label}** · {sum(r.get('new', 0) for r in runs)} חדשות · {open_n} עדיין פתוחות  \n"
                       + " ".join(f"<span class='tag t-kw'>{lbl(r['kw'])} · {r.get('new', 0)}</span>" for r in runs), unsafe_allow_html=True)
            if c.button("פתח", key=f"h-{g}", width="stretch", disabled=not open_n):
                st.session_state.hist = {"keys": keys, "label": label}
                st.switch_page(PAGES["inbox"])

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
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("נשלחו היום", per_day[today], f"יעד {goal}")
    m2.metric("נשלחו ב-7 ימים", sum(v for d, v in per_day.items() if d > today - dt.timedelta(days=7)))
    m3.metric("נשלחו בסך הכל", sum(1 for a in arc.values() if a.get("status") == "sent"))
    usd, cap_usd = apify_usage()
    m4.metric("Apify החודש", f"${usd:.2f}" if usd is not None else "?", f"מתוך ${cap_usd:.0f}" if cap_usd else None, delta_color="off")
    st.markdown("##### הגשות ביום (14 ימים)")
    st.bar_chart(pd.DataFrame({"נשלחו": list(per_day.values())}, index=[d.strftime("%d.%m") for d in days]), height=220)

    st.markdown("##### לפי מילה")
    rows = []
    for k in list(dict.fromkeys(KEYWORDS + [r["kw"] for r in s.get("runs", [])])):
        runs = [r for r in s.get("runs", []) if r["kw"] == k]
        js = [j for j in jobs.values() if k in j.get("kws", [])]
        if not runs and not js:
            continue
        rows.append({"מילה": lbl(k), "חיפושים": len(runs), "משרות": len(js), "ציון 45+": sum(1 for j in js if (j.get("fit") or 0) >= 45),
                     "ציון 70+": sum(1 for j in js if (j.get("fit") or 0) >= 70),
                     "נשלחו": sum(1 for j in js if (arc.get(j["key"]) or {}).get("status") == "sent"),
                     "הוסרו": sum(1 for j in js if (arc.get(j["key"]) or {}).get("status") == "removed")})
    st.dataframe(pd.DataFrame(rows).sort_values("נשלחו", ascending=False), hide_index=True, width="stretch",
                 column_config={"נשלחו": st.column_config.ProgressColumn("נשלחו", min_value=0, max_value=max([r["נשלחו"] for r in rows] + [1]), format="%d")})

    st.markdown("##### לפי מקור")
    src = {}
    for j in jobs.values():
        for b in j.get("boards", []):
            o = src.setdefault(b, {"מקור": BOARD.get(b, b), "משרות": 0, "ציון 45+": 0, "נשלחו": 0})
            o["משרות"] += 1
            o["ציון 45+"] += (j.get("fit") or 0) >= 45
            o["נשלחו"] += (arc.get(j["key"]) or {}).get("status") == "sent"
    st.dataframe(pd.DataFrame(sorted(src.values(), key=lambda o: -o["נשלחו"])), hide_index=True, width="stretch")
    st.caption("עלות משוערת: כ-$0.25 לכל מילת חיפוש (Apify כ-$0.16 + Claude כ-$0.09).")


# ---------------- app shell ----------------
state, _ = load_state()
PAGES = {
    "inbox": st.Page(page_inbox, title="משרות", icon=":material/inbox:", url_path="inbox", default=True),
    "search": st.Page(page_search, title="חיפוש חדש", icon=":material/search:", url_path="search"),
    "history": st.Page(page_history, title="היסטוריה", icon=":material/history:", url_path="history"),
    "archive": st.Page(page_archive, title="ארכיון", icon=":material/inventory_2:", url_path="archive"),
    "stats": st.Page(page_stats, title="סטטיסטיקה", icon=":material/insights:", url_path="stats"),
}
nav = st.navigation(list(PAGES.values()), position="sidebar")
with st.sidebar:
    st.markdown("<div class='brand'>Job Scout<small>חיפוש עבודה · ישראל</small></div>", unsafe_allow_html=True)
    goal = int(secret("DAILY_GOAL", 50))
    sent_n = sent_today(state)
    st.progress(min(1.0, sent_n / max(1, goal)), text=f"נשלחו היום **{sent_n}** / {goal}")
    open_n = sum(1 for j in state["jobs"].values() if j["key"] not in state["archive"] and j["key"] not in state.get("queue", {}))
    cap = int(secret("MAX_SEARCHES_PER_DAY", 30))
    usd, cap_usd = apify_usage()
    st.markdown(f"<div class='side-kpi'><span>משרות פתוחות</span><b>{open_n}</b></div>"
                f"<div class='side-kpi'><span>חיפושים היום</span><b>{runs_today(state)}/{cap}</b></div>"
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
