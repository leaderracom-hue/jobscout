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

import requests
import streamlit as st

APP = Path(__file__).parent
IL = ZoneInfo("Asia/Jerusalem")
GH = "https://api.github.com"
KEYWORDS = ["product ai", "marketing automation", "product marketing", "gtm product", "gtm ai", "gtm engineer", "ai builder", "ai automation",
            "automation", "crm integration", "crm automation", "ai trainer", "freelancer", "מנהל מוצר", "מנהל מוצר אוטומציות", "אוטומציות שיווק", "מנהל פרויקט אוטומציות"]
LABEL = {"product ai": "Product AI", "marketing automation": "Marketing Automation", "product marketing": "Product Marketing",
         "gtm product": "GTM Product", "gtm ai": "GTM AI", "gtm engineer": "GTM Engineer", "ai builder": "AI Builder", "ai automation": "AI Automation", "automation": "Automation",
         "crm integration": "CRM Integration", "crm automation": "CRM Automation", "ai trainer": "AI Trainer", "freelancer": "Freelancer", "מנהל מוצר": "מנהל מוצר", "מנהל מוצר אוטומציות": "מנהל מוצר אוטומציות", "אוטומציות שיווק": "אוטומציות שיווק", "מנהל פרויקט אוטומציות": "מנהל פרויקט אוטומציות"}
WINDOWS = {24: "24 שעות", 48: "48 שעות", 72: "72 שעות", 0: "שבוע"}
MIN_FIT = 45  # Assaf 11 Nov: a score under 45 goes straight to the archive ("auto")
HISTORY_HOURS = 72  # Assaf: past searches can be reopened for 72 hours
BOARD = {"linkedin": "LinkedIn", "indeed": "Indeed", "drushim": "דרושים", "alljobs": "AllJobs", "careers": "דף קריירה"}

st.set_page_config(page_title="Job Scout", page_icon=":material/work:", layout="wide")
st.markdown("""<style>
.block-container{max-width:1050px;padding-top:2rem}
.stMainBlockContainer, .stMarkdown, .stCaption, [data-testid="stExpander"]{direction:rtl;text-align:right}
.fit{width:58px;height:58px;border-radius:12px;display:flex;flex-direction:column;align-items:center;justify-content:center;
     font-family:ui-monospace,monospace;font-weight:600;font-size:1.3rem;line-height:1}
.fit small{font-size:.6rem;font-weight:500;margin-top:3px;font-family:sans-serif}
.f-hi{background:#e3f3ea;color:#16794a}.f-mid{background:#fbf0dc;color:#9a5b00}.f-lo{background:#fbe7e6;color:#b3322f}.f-na{background:#eef0f3;color:#5b6474}
.jt{font-weight:600;font-size:1.05rem}.jt a{color:inherit;text-decoration:none}
.sub{color:#6b7280;font-size:.88rem}
.tag{display:inline-block;font-size:.72rem;border:1px solid #dde1e8;border-radius:999px;padding:0 9px;margin:2px 0 0 4px;color:#5b6474}
.cv{display:inline-block;font-size:.78rem;background:#e7edfb;color:#2a55c9;border-radius:6px;padding:2px 8px;font-family:ui-monospace,monospace;margin-left:4px}
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


# ---------------- screen ----------------
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


def mark(key, status):
    def m(s):
        j = s["jobs"].get(key, {})
        if status == "restore":
            s["archive"].pop(key, None)
        else:
            s["archive"][key] = {"status": status, "at": dt.datetime.now(dt.timezone.utc).isoformat(), "title": j.get("title"),
                                 "company": j.get("company"), "urls": list((j.get("links") or {"x": j.get("url")}).values())}
    update_state(m, f"{status}: {key}")


QSTATUS = {"queued": ("ממתין לסוכן", "#5b6474"), "ready": ("מולא · ממתין שתלחץ שלח", "#2a55c9"),
           "needs_you": ("צריך אותך", "#9a5b00"), "unsupported": ("אתר שעוד לא נתמך", "#9a5b00"),
           "skipped": ("נסגר בלי שליחה", "#5b6474"), "failed": ("נכשל", "#b3322f")}


def queue_set(key, status=None):
    """status None = take the job out of the send queue."""
    def m(s):
        q = s.setdefault("queue", {})
        if status is None:
            q.pop(key, None)
        else:
            q[key] = {**q.get(key, {}), "status": status, "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    update_state(m, f"queue {status or 'remove'}: {key}")


def card(j, arc=False, queued=None):
    fit = j.get("fit")
    cls = "f-na" if fit is None else "f-hi" if fit >= 80 else "f-mid" if fit >= 60 else "f-lo"
    with st.container(border=True):
        c1, c2, c3 = st.columns([1, 7, 3], vertical_alignment="top")
        c1.markdown(f'<div class="fit {cls}">{fit if fit is not None else "—"}<small>התאמה</small></div>', unsafe_allow_html=True)
        tags = "".join(f'<span class="tag">{BOARD.get(b, b)}</span>' for b in j.get("boards", []))
        if j.get("easyApply"):
            tags += '<span class="tag" style="color:#16794a">Easy Apply</span>'
        if arc:
            label = {"sent": "נשלח", "auto": "הוסר אוטומטית (סייבר / אבטחה)" if j.get("reason") == "cyber" else "הוסר אוטומטית (ציון מתחת ל-45)"}.get(j.get("status"), "הוסר")
            if j.get("by") == "agent":
                label += " · ע״י הסוכן"
            tags += f'<span class="tag">{label}</span>'
        if queued:
            lbl, col = QSTATUS.get(queued.get("status"), (queued.get("status"), "#5b6474"))
            note = f' — {queued["note"]}' if queued.get("note") else ""
            tags += f'<span class="tag" style="color:{col};border-color:{col}">{lbl}{note}</span>'
        cv = j.get("cv") or ""
        lang = "עברית" if j.get("lang") == "he" else "אנגלית"
        why = j.get("why") or (f"הדירוג נכשל: {j['scoreError']}" if j.get("scoreError") else "")
        c2.markdown(
            f'<div class="jt"><a href="{j.get("url")}" target="_blank">{j.get("title")}</a></div>'
            f'<div class="sub">{j.get("company") or "—"} · {j.get("location") or "ישראל"} · פורסם {ago(j)}</div>'
            f'<div style="font-size:.9rem;margin:4px 0">{why}</div>'
            f'<div>{f"<span class=cv>קו״ח {lang}: {cv}</span>" if cv else ""}{tags}</div>', unsafe_allow_html=True)
        k = j["key"]
        if arc:
            if c3.button("החזר", key=f"r-{k}"):
                mark(k, "restore")
                st.rerun()
            return
        if queued:
            c3.link_button("פתח משרה", j.get("url") or "#", use_container_width=True)
            if c3.button("הוצא מהתור", key=f"q-{k}", use_container_width=True):
                queue_set(k, None)
                st.rerun()
            if queued.get("status") in ("needs_you", "skipped", "failed", "unsupported") and c3.button("נסה שוב", key=f"qr-{k}", use_container_width=True):
                queue_set(k, "queued")
                st.rerun()
            return
        if c3.button("לשליחה ←", key=f"qa-{k}", type="primary", use_container_width=True):
            queue_set(k, "queued")
            st.rerun()
        c3.link_button("פתח משרה", j.get("url") or "#", use_container_width=True)
        if cv:
            if st.session_state.get("cv_open") == k:
                try:
                    c3.download_button("הורד קו״ח", cv_bytes(j.get("lang", "en"), cv), file_name=cv, mime="application/pdf",
                                       key=f"d-{k}", use_container_width=True)
                except Exception:
                    c3.caption("הקובץ לא נמצא במאגר")
            elif c3.button("קו״ח", key=f"c-{k}", use_container_width=True):
                st.session_state.cv_open = k
                st.rerun()
        a, b = c3.columns(2)
        if a.button("שלחתי", key=f"s-{k}", use_container_width=True):
            mark(k, "sent")
            st.rerun()
        if b.button("הסר", key=f"x-{k}", use_container_width=True):
            mark(k, "removed")
            st.rerun()


state, _ = load_state()
cap = int(secret("MAX_SEARCHES_PER_DAY", 25))  # Assaf 12 Nov: 25 a day (the old MAX_RUNS_PER_DAY=10 secret is no longer read)
used = runs_today(state)

top_l, top_r = st.columns([4, 1])
top_l.title("Job Scout")
if top_r.button("יציאה"):
    st.session_state.clear()
    st.rerun()

picked = st.pills("מילות חיפוש (אפשר כמה)", KEYWORDS, format_func=lambda k: LABEL.get(k, k), selection_mode="multi",
                  default=st.session_state.get("kws_picked", ["marketing automation"]), key="kw_pick") or []
free = st.text_input("מילים נוספות (מופרדות בפסיק)", key="kw_free", placeholder="למשל: lifecycle marketing, מנהל CRM")
kws = list(dict.fromkeys(list(picked) + [w.strip().lower() for w in free.split(",") if w.strip()]))
st.session_state.kws_picked = list(picked)
c1, c2 = st.columns(2)
win = c1.segmented_control("פורסם ב-", list(WINDOWS), format_func=lambda h: WINDOWS[h], default=48, key="win") or 0
sort = c2.segmented_control("מיון", ["fit", "new"], format_func=lambda s: "התאמה" if s == "fit" else "הכי חדש", default="fit", key="sort") or "fit"

PHASE = {"queued": "ממתין", "pulling": "מושך משרות", "scoring": "מדרג וקורות חיים", "done": "הסתיים", "error": "נכשל"}
active = [i for i in SH["batches"].values() if not i["done"]]
pending = sum(1 for i in active for v in i["kw"].values() if v.get("phase") not in ("done", "error"))
left = cap - used - pending
names = " + ".join(LABEL.get(k, k) for k in kws) or "בחר מילה"
r1, r2 = st.columns([2, 3], vertical_alignment="center")
mybatch = SH["batches"].get(st.session_state.get("batch") or "")
running = bool(mybatch and not mybatch["done"])
if r1.button(f"חפש · {names} · {WINDOWS[win]}", type="primary", disabled=running or not kws or len(kws) > left, use_container_width=True):
    st.session_state.batch = start_batch(kws, win)  # runs in the background; results appear below while it works
    st.session_state.pop("hist", None)
    st.rerun()
r2.caption(f"~$0.15–0.30 לכל מילת חיפוש · נבחרו {len(kws)} · היום {used + pending}/{cap} חיפושים"
           + (" · יותר מילים ממה שנשאר היום" if len(kws) > left else ""))


def batch_progress(info):
    secs = int((info.get("ended") or time.time()) - info["started"])
    lines = []
    for k in info["kws"]:
        v = info["kw"][k]
        extra = ""
        if v.get("phase") == "scoring" and v.get("new") is not None:
            extra = f" · {v.get('scored', 0)}/{v['new']}"
        if v.get("phase") == "done":
            n, a = v.get("new", 0), v.get("auto", 0)
            if n and a >= n:
                extra = f" · {n} חדשות, כולן הוסרו אוטומטית (ציון מתחת ל-{MIN_FIT}), אין מה להציג"
            else:
                extra = f" · {n} חדשות" + (f", {a} הוסרו אוטומטית (מתחת ל-{MIN_FIT})" if a else "")
        if v.get("phase") == "error":
            extra = f" · {str(v.get('error', ''))[:80]}"
        icon = {"done": "✓", "error": "✗"}.get(v.get("phase"), "…")
        lines.append(f"{icon} **{LABEL.get(k, k)}** · {PHASE.get(v.get('phase'), v.get('phase'))}{extra}")
    head = f"{'הסתיים' if info['done'] else 'מחפש'} · {secs // 60}:{secs % 60:02d} דק'"
    return head, lines


def kw_history(s, kw):
    """History page for ONE search keyword: every run, and every job it ever found (open, queued, sent, removed, auto-removed)."""
    queue = s.get("queue", {})
    c_a, c_b = st.columns([5, 1], vertical_alignment="center")
    c_a.subheader(f"היסטוריה · {LABEL.get(kw, kw)}")
    if c_b.button("חזור", key="kwh_back", use_container_width=True):
        st.session_state.pop("kwhist", None)
        st.rerun()
    runs = [r for r in s.get("runs", []) if r["kw"] == kw]
    jobs = [j for j in s["jobs"].values() if kw in j.get("kws", [])]
    opn, auto, done = [], [], []
    for j in jobs:
        k = j["key"]
        a = s["archive"].get(k)
        if a:
            row = {**j, **a, "key": k}
            (auto if a.get("status") == "auto" else done).append(row)
        elif k not in queue:
            opn.append(j)
    qj = [{**j, "_q": queue[j["key"]]} for j in jobs if j["key"] in queue and j["key"] not in s["archive"]]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("ריצות", len(runs))
    m2.metric("משרות שנמצאו", len(jobs))
    m3.metric("פתוחות", len(opn) + len(qj))
    m4.metric(f"הוסרו אוטומטית (<{MIN_FIT})", len(auto))
    if runs:
        st.markdown("**ריצות**")
        for r in runs:
            at = dt.datetime.fromisoformat(r["at"].replace("Z", "+00:00")).astimezone(IL).strftime("%d.%m %H:%M")
            per = " · ".join(f"{BOARD.get(b, b)} {v.get('kept', 0)}/{v.get('pulled', 0)}" for b, v in (r.get("stats") or {}).items())
            note = f" · {r['autoRemoved']} הוסרו אוטומטית" if r.get("autoRemoved") else ""
            st.caption(f"{at} · {WINDOWS.get(r.get('hours') or 0, 'שבוע')} · {r['new']} חדשות{note} · Apify ${r.get('apifyUsd') or '?'} + Claude ${r.get('claudeUsd')}"
                       + (f"  \nלפי מקור (נכנסו/נמשכו): {per}" if per else ""))
    else:
        st.info("אין עדיין ריצות שמורות למילה הזאת.")
    tabs = st.tabs([f"פתוחות ({len(opn) + len(qj)})", f"הוסרו אוטומטית ({len(auto)})", f"נשלחו / הוסרו ({len(done)})"])
    with tabs[0]:
        if not (opn or qj):
            st.info("אין משרות פתוחות למילה הזאת.")
        for j in sorted(opn, key=lambda j: -(j.get("fit") if j.get("fit") is not None else -1)):
            card(j)
        for j in qj:
            card(j, queued=j["_q"])
    with tabs[1]:
        st.caption(f"משרות שקיבלו ציון מתחת ל-{MIN_FIT} ולא מוצגות בתוצאות. אפשר להחזיר כל אחת.")
        if not auto:
            st.info("אין משרות שהוסרו אוטומטית.")
        for j in sorted(auto, key=lambda j: j.get("at", ""), reverse=True):
            card(j, arc=True)
    with tabs[2]:
        if not done:
            st.info("לא סימנת עדיין משרות של המילה הזאת.")
        for j in sorted(done, key=lambda j: j.get("at", ""), reverse=True):
            card(j, arc=True)


def body():
    s, _ = load_state()
    if st.session_state.get("kwhist"):
        kw_history(s, st.session_state.kwhist)
        return
    info = SH["batches"].get(st.session_state.get("batch") or "")
    if info:
        head, lines = batch_progress(info)
        with st.container(border=True):
            st.markdown(f"**{head}**")
            st.markdown("  \n".join(lines))
            if info["done"] and st.button("סגור", key="close_batch"):
                st.session_state.pop("batch", None)
                st.rerun()
        if info["done"] and not info.get("seen"):
            info["seen"] = True
            st.rerun(scope="app")  # refresh the counters at the top once the batch ends
    view_kws = list(dict.fromkeys(kws + (info["kws"] if info else [])))
    for k in view_kws:
        last = next((r for r in s.get("runs", []) if r["kw"] == k), None)
        if not last:
            continue
        at = dt.datetime.fromisoformat(last["at"].replace("Z", "+00:00")).astimezone(IL).strftime("%d.%m %H:%M")
        auto_note = f" ({last['autoRemoved']} מתחת ל-45 הוסרו)" if last.get("autoRemoved") else ""
        per = " · ".join(f"{BOARD.get(b, b)} {v.get('kept', 0)}/{v.get('pulled', 0)}" for b, v in (last.get("stats") or {}).items())
        c_l, c_h = st.columns([5, 1], vertical_alignment="center")
        c_l.caption(f"ריצה אחרונה ל-{LABEL.get(k, k)}: {at} · {last['new']} חדשות{auto_note} · Apify ${last.get('apifyUsd') or '?'} + Claude ${last.get('claudeUsd')}"
                    + (f"  \nלפי מקור (נכנסו/נמשכו): {per}" if per else ""))
        if c_h.button("היסטוריה", key=f"kh-{k}", use_container_width=True):
            st.session_state.kwhist = k
            st.rerun()

    queue = s.get("queue", {})
    hist = st.session_state.get("hist")
    if hist:  # a past search reopened from the history tab
        hk = set(hist["keys"])
        mine = [j for k, j in s["jobs"].items() if k in hk and k not in s["archive"] and k not in queue]
        shown = list(mine)
        c_a, c_b = st.columns([4, 1], vertical_alignment="center")
        c_a.info(f"מציג תוצאות שמורות: {hist['label']} · {len(mine)} פתוחות מתוך {len(hk)}")
        if c_b.button("חזור", key="hist_back"):
            st.session_state.pop("hist", None)
            st.rerun()
    else:
        mine = [j for j in s["jobs"].values() if j["key"] not in s["archive"] and j["key"] not in queue and any(k in j.get("kws", []) for k in view_kws)]
        shown = [j for j in mine if not win or age_h(j) <= win + (0 if j.get("dayOnly") else 1)]
    shown.sort(key=(lambda j: (-(j.get("fit") if j.get("fit") is not None else -1), age_h(j))) if sort == "fit" else age_h)
    qjobs = sorted(({**s["jobs"].get(k, {"title": "?"}), "key": k, "_q": q} for k, q in queue.items() if k not in s["archive"]),
                   key=lambda j: j["_q"].get("at", ""), reverse=True)
    arc = sorted(({**s["jobs"].get(k, {}), **a, "key": k} for k, a in s["archive"].items()), key=lambda j: j.get("at", ""), reverse=True)
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=HISTORY_HOURS)).isoformat()
    groups = {}
    for r in s.get("runs", []):
        if r.get("keys") and r["at"] >= cutoff:
            groups.setdefault(r.get("batch") or r["at"], []).append(r)

    t1, tq, th, t2 = st.tabs([f"תוצאות ({len(shown)})", f"תור שליחה ({len(qjobs)})", f"היסטוריה ({len(groups)})", f"ארכיון ({len(arc)})"])
    with t1:
        if not shown:
            if running:
                st.info("החיפוש רץ. משרות חדשות יופיעו כאן ברגע שכל מילה מסתיימת.")
            elif hist:
                st.info("כל המשרות מהחיפוש הזה כבר בארכיון או בתור.")
            else:
                hidden = len(mine)
                ran = any(r["kw"] in view_kws for r in s.get("runs", []))
                lastauto = sum((next((r for r in s.get("runs", []) if r["kw"] == k), {}).get("autoRemoved") or 0) for k in view_kws)
                if hidden:
                    st.info(f"אין משרות מה-{WINDOWS[win]} האחרונות. {hidden} ישנות יותר מוסתרות.")
                elif lastauto:
                    st.info(f"הריצה האחרונה מצאה {lastauto} משרות חדשות, אבל כולן קיבלו ציון מתחת ל-{MIN_FIT} והוסרו אוטומטית, לכן אין מה להציג. "
                            f"לחץ \"היסטוריה\" ליד המילה כדי לראות אותן ולהחזיר.")
                else:
                    st.info("אין משרות פתוחות למילים שבחרת." if ran else "עוד לא חיפשת את המילים האלה. החיפוש ירוץ רק כשתלחץ \"חפש\".")
        for j in shown:
            card(j)
        if not hist and shown and len(mine) > len(shown):
            st.caption(f"{len(mine) - len(shown)} משרות ישנות יותר מ-{WINDOWS[win]} מוסתרות")
    with tq:
        if not qjobs:
            st.info("התור ריק. לחץ \"לשליחה ←\" על משרה בתוצאות, ואז הפעל את סוכן השליחה במחשב שלך.")
        else:
            st.caption("סוכן השליחה רץ במחשב שלך: `node job-engine/apply-agent/apply.mjs`. הוא ממלא את הטופס ועוצר; אתה לוחץ \"שלח\" בדפדפן שלו.")
        for j in qjobs:
            card(j, queued=j["_q"])
    with th:
        st.caption(f"חיפושים מ-{HISTORY_HOURS} השעות האחרונות. פתיחה טוענת את התוצאות מהזיכרון, בלי חיפוש ובלי עלות.")
        if not groups:
            st.info("אין עדיין חיפושים שמורים. חיפושים מעכשיו והלאה יישמרו כאן ל-72 שעות.")
        for g, runs in sorted(groups.items(), key=lambda x: x[1][0]["at"], reverse=True):
            at = dt.datetime.fromisoformat(runs[0]["at"].replace("Z", "+00:00")).astimezone(IL).strftime("%d.%m %H:%M")
            keys = list(dict.fromkeys(k for r in runs for k in r["keys"]))
            open_n = sum(1 for k in keys if k not in s["archive"] and k not in queue)
            label = f"{at} · {' + '.join(LABEL.get(r['kw'], r['kw']) for r in runs)} · {WINDOWS.get(runs[0].get('hours') or 0, 'שבוע')}"
            a, b = st.columns([4, 1], vertical_alignment="center")
            a.markdown(f"**{label}**  \n{len(keys)} משרות · {open_n} עדיין פתוחות")
            if b.button("פתח", key=f"h-{g}", use_container_width=True):
                st.session_state.hist = {"keys": keys, "label": label}
                st.rerun()
    with t2:
        if not arc:
            st.info("הארכיון ריק. משרות שתסמן \"שלחתי\" או \"הסר\" יגיעו לכאן ולא יופיעו שוב בחיפושים.")
        for j in arc:
            card(j, arc=True)


st.fragment(run_every=4 if running else None)(body)()
