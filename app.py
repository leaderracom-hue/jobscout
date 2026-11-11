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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import streamlit as st

APP = Path(__file__).parent
IL = ZoneInfo("Asia/Jerusalem")
GH = "https://api.github.com"
KEYWORDS = ["product ai", "marketing automation", "product marketing", "gtm product", "ai builder", "ai automation",
            "automation", "crm integration", "crm automation", "ai trainer", "freelancer"]
LABEL = {"product ai": "Product AI", "marketing automation": "Marketing Automation", "product marketing": "Product Marketing",
         "gtm product": "GTM Product", "ai builder": "AI Builder", "ai automation": "AI Automation", "automation": "Automation",
         "crm integration": "CRM Integration", "crm automation": "CRM Automation", "ai trainer": "AI Trainer", "freelancer": "Freelancer"}
WINDOWS = {24: "24 שעות", 48: "48 שעות", 72: "72 שעות", 0: "שבוע"}
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


def secret(name, default=None):
    try:
        return st.secrets[name]
    except Exception:
        return default


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
    # GitHub may serve a read up to ~60 s old right after a write, so the copy this session just wrote wins for 90 s.
    c = st.session_state.get("state_cache")
    if c and time.time() - c[2] < 90:
        return json.loads(json.dumps(c[0])), c[1]
    r = requests.get(gh_url("state.json"), headers=gh_headers(), timeout=20)
    if r.status_code == 404:
        return {"jobs": {}, "archive": {}, "runs": []}, None
    r.raise_for_status()
    meta = r.json()
    raw = base64.b64decode(meta["content"]) if meta.get("content") else requests.get(gh_url("state.json"), headers=gh_headers(True), timeout=30).content
    return json.loads(raw), meta["sha"]


def update_state(mutate, message):
    """Read fresh, apply the change, write back; retry if another write landed in between."""
    for _ in range(4):
        s, sha = load_state()
        mutate(s)
        body = {"message": message, "content": base64.b64encode(json.dumps(s, ensure_ascii=False, indent=1).encode()).decode()}
        if sha:
            body["sha"] = sha
        r = requests.put(gh_url("state.json"), headers=gh_headers(), json=body, timeout=30)
        if r.status_code in (200, 201):
            st.session_state.state_cache = (s, r.json()["content"]["sha"], time.time())
            return s
        st.session_state.pop("state_cache", None)
        if r.status_code not in (409, 422):
            r.raise_for_status()
        time.sleep(0.8)
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


def do_search(kw, hours):
    days = hours // 24 if hours in (24, 48, 72) else 7
    with st.status(f"מחפש {LABEL[kw]} · {WINDOWS[hours]}…", expanded=True) as box:
        box.write("מושך משרות מ-LinkedIn, Indeed, AllJobs, דרושים ודפי קריירה (כ-2–3 דקות)…")
        out = tempfile.mkdtemp()
        env = {**os.environ, "APIFY_API_TOKEN": secret("APIFY_API_TOKEN", ""), "JOBENGINE_SERVER": "1", "JOBENGINE_RUNS_DIR": tempfile.gettempdir()}
        p = subprocess.run(["node", str(APP / "engine/mini-agent/scout.mjs"), kw, "--out", out, "--days", str(days)],
                           cwd=str(APP / "engine"), env=env, capture_output=True, text=True, timeout=900)
        if p.returncode != 0:
            box.update(label="החיפוש נכשל", state="error")
            st.code((p.stdout + p.stderr)[-1500:])
            return
        found = json.loads(Path(out, "results-full.json").read_text(encoding="utf-8"))
        spent = re.search(r'"spentUsd":([\d.]+)', p.stdout)
        s, _ = load_state()
        fresh, seen_again = [], {}
        for j in found:
            if archived(s, j):
                continue
            k = key_of(j)
            if k in s["jobs"]:
                seen_again[k] = j
            else:
                fresh.append((k, j))
        box.write(f"נמצאו {len(found)} משרות, {len(fresh)} חדשות. מדרג ובוחר קורות חיים…")
        bar = box.progress(0.0)
        scores, usd = {}, 0.0
        with ThreadPoolExecutor(4) as ex:
            futs = {ex.submit(score_job, j, kw): k for k, j in fresh}
            for i, f in enumerate(as_completed(futs), 1):
                k = futs[f]
                try:
                    sc = f.result()
                    usd += sc.pop("usd")
                    scores[k] = sc
                except Exception as e:
                    scores[k] = {"scoreError": str(e)[:160]}
                bar.progress(i / max(1, len(fresh)))
        now = dt.datetime.now(dt.timezone.utc).isoformat()

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
            st_["runs"] = [{"kw": kw, "hours": hours or None, "at": now, "found": len(found), "new": len(fresh),
                            "apifyUsd": float(spent.group(1)) if spent else None, "claudeUsd": round(usd, 3)}] + st_.get("runs", [])[:199]

        update_state(mutate, f"run {kw} {hours or 'week'}h: {len(fresh)} new")
        box.update(label=f"הסתיים: {len(fresh)} משרות חדשות · Claude ${usd:.2f}", state="complete", expanded=False)


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


def card(j, arc=False):
    fit = j.get("fit")
    cls = "f-na" if fit is None else "f-hi" if fit >= 80 else "f-mid" if fit >= 60 else "f-lo"
    with st.container(border=True):
        c1, c2, c3 = st.columns([1, 7, 3], vertical_alignment="top")
        c1.markdown(f'<div class="fit {cls}">{fit if fit is not None else "—"}<small>התאמה</small></div>', unsafe_allow_html=True)
        tags = "".join(f'<span class="tag">{BOARD.get(b, b)}</span>' for b in j.get("boards", []))
        if j.get("easyApply"):
            tags += '<span class="tag" style="color:#16794a">Easy Apply</span>'
        if arc:
            tags += f'<span class="tag">{"נשלח" if j.get("status") == "sent" else "הוסר"}</span>'
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
        if a.button("שלחתי", key=f"s-{k}", type="primary", use_container_width=True):
            mark(k, "sent")
            st.rerun()
        if b.button("הסר", key=f"x-{k}", use_container_width=True):
            mark(k, "removed")
            st.rerun()


state, _ = load_state()
cap = int(secret("MAX_RUNS_PER_DAY", 10))
used = runs_today(state)

top_l, top_r = st.columns([4, 1])
top_l.title("Job Scout")
if top_r.button("יציאה"):
    st.session_state.clear()
    st.rerun()

kw = st.pills("מילת חיפוש", KEYWORDS, format_func=lambda k: LABEL[k], default=st.session_state.get("kw", "marketing automation"), key="kw_pick") or "marketing automation"
st.session_state.kw = kw
c1, c2 = st.columns(2)
win = c1.segmented_control("פורסם ב-", list(WINDOWS), format_func=lambda h: WINDOWS[h], default=48, key="win") or 0
sort = c2.segmented_control("מיון", ["fit", "new"], format_func=lambda s: "התאמה" if s == "fit" else "הכי חדש", default="fit", key="sort") or "fit"

r1, r2 = st.columns([2, 3], vertical_alignment="center")
if r1.button(f"חפש · {LABEL[kw]} · {WINDOWS[win]}", type="primary", disabled=used >= cap, use_container_width=True):
    do_search(kw, win)
    state, _ = load_state()
    used = runs_today(state)
r2.caption(f"~$0.10–0.20 משיכה + ~$0.0055 לכל משרה חדשה · היום {used}/{cap} חיפושים"
           + (" · הגעת למכסה היומית" if used >= cap else ""))
last = next((r for r in state.get("runs", []) if r["kw"] == kw), None)
if last:
    at = dt.datetime.fromisoformat(last["at"].replace("Z", "+00:00")).astimezone(IL).strftime("%d.%m %H:%M")
    st.caption(f"ריצה אחרונה ל-{LABEL[kw]}: {at} · {last['new']} חדשות · Apify ${last.get('apifyUsd') or '?'} + Claude ${last.get('claudeUsd')}")

mine = [j for j in state["jobs"].values() if j["key"] not in state["archive"] and kw in j.get("kws", [])]
shown = [j for j in mine if not win or age_h(j) <= win + (0 if j.get("dayOnly") else 1)]
shown.sort(key=(lambda j: (-(j.get("fit") if j.get("fit") is not None else -1), age_h(j))) if sort == "fit" else age_h)
arc = sorted(({**state["jobs"].get(k, {}), **a, "key": k} for k, a in state["archive"].items()), key=lambda j: j.get("at", ""), reverse=True)

t1, t2 = st.tabs([f"תוצאות ({len(shown)})", f"ארכיון ({len(arc)})"])
with t1:
    if not shown:
        hidden = len(mine)
        st.info(f"אין משרות מה-{WINDOWS[win]} האחרונות. {hidden} ישנות יותר מוסתרות." if hidden
                else (f"אין משרות פתוחות ל-{LABEL[kw]}." if last else f"עוד לא חיפשת {LABEL[kw]}. החיפוש ירוץ רק כשתלחץ \"חפש\"."))
    for j in shown:
        card(j)
    if shown and len(mine) > len(shown):
        st.caption(f"{len(mine) - len(shown)} משרות ישנות יותר מ-{WINDOWS[win]} מוסתרות")
with t2:
    if not arc:
        st.info("הארכיון ריק. משרות שתסמן \"שלחתי\" או \"הסר\" יגיעו לכאן ולא יופיעו שוב בחיפושים.")
    for j in arc:
        card(j, arc=True)
