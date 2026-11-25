"""Job rules with no Streamlit and no network: keys, exclusions (cyber, medical, user words), Israel / abroad, language, EU countries,
eligibility from the user's preferences and the final score. Imported by app.py; covered by tests/test_rules.py."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DICT = json.loads((ROOT / "engine/mini-agent/dictionary.json").read_text(encoding="utf-8"))


def _list_rx(words):
    parts = [rf"(?<![a-z0-9]){re.escape(w.lower())}(?![a-z0-9])" if all(ord(c) < 128 for c in w) else re.escape(w) for w in words if w]
    return re.compile("|".join(parts), re.I) if parts else None


CYBER_CO = _list_rx((DICT.get("_exclude") or {}).get("companies", []))
CYBER_TI = _list_rx((DICT.get("_exclude") or {}).get("titles", []))


MED_CO = _list_rx((DICT.get("_exclude_medical") or {}).get("companies", []))
MED_TI = _list_rx((DICT.get("_exclude_medical") or {}).get("titles", []))


def is_medical(j):
    """Assaf 24 Nov: no medical / healthcare-clinical jobs (title words and company names; the engine also checks the LinkedIn industry)."""
    return bool((MED_CO and MED_CO.search(str(j.get("company") or "").lower())) or (MED_TI and MED_TI.search(str(j.get("title") or "").lower())))


def is_cyber(j):
    """Safety net in the app (Assaf 19 Nov): the engine's cyber lists, checked again here. Cyber beats every other rule, GTM included."""
    return bool((CYBER_CO and CYBER_CO.search(str(j.get("company") or "").lower())) or (CYBER_TI and CYBER_TI.search(str(j.get("title") or "").lower())))


def norm(s):
    return re.sub(r"[^a-z֐-׿0-9]", "", str(s or "").lower())


def key_of(j):
    return norm(j.get("company"))[:10] + "|" + norm(j.get("title"))[:24]


def settings_of(s):
    """Assaf 19 Nov: user rules kept in state.json → exclude words (title), blocked + followed companies."""
    st_ = s.setdefault("settings", {})
    for k in ("exclude_words", "keep_words", "blocked_companies", "followed_companies"):
        st_.setdefault(k, [])
    return st_


def company_key(name):
    n = norm(name)
    return "" if not n or "חסוי" in str(name or "") or n in ("confidential",) else n[:24]


def _word_hit(word, text):
    w = str(word or "").strip()
    if not w:
        return False
    if re.fullmatch(r"[\x00-\x7f]+", w):  # Latin: whole word only ("staff" must not hit "staffing")
        return re.search(r"(?<![a-z0-9])" + re.escape(w.lower()) + r"(?![a-z0-9])", str(text or "").lower()) is not None
    return w in str(text or "")


GTM_RX = re.compile(r"\bgtm\b", re.I)


def is_gtm(j):
    """Assaf 19 Nov: a job with GTM in the title or the post text skips the title filters and the exclude words.
    The score rule still wins: under MIN_FIT (40) is never shown (Assaf, later the same day)."""
    return bool(j.get("gtm") or GTM_RX.search(str(j.get("title") or "")) or GTM_RX.search(str(j.get("desc") or "")))


def excluded(s, j):
    """Why a job is filtered out by the user's own rules, or None."""
    cfg = settings_of(s)
    if is_cyber(j):
        return "סייבר"
    if is_medical(j):
        return "רפואה"
    ck = company_key(j.get("company"))
    if ck and ck in cfg["blocked_companies"]:
        return f"חברה חסומה: {j.get('company')}"
    if is_gtm(j) or any(_word_hit(w, j.get("title")) for w in cfg["keep_words"]):  # GTM jobs + "never hide" words win over exclude words
        return None
    for w in cfg["exclude_words"]:
        if _word_hit(w, j.get("title")):
            return f"מילה לסינון: {w}"
    return None


IL_PLACES = re.compile("israel|ישראל|tel aviv|tel-aviv|tel aviv-yafo|תל אביב|herzliya|הרצליה|ramat gan|רמת גן|petah tikva|petach tikva|פתח תקווה|"
                       "haifa|חיפה|jerusalem|ירושלים|raanana|ra'anana|רעננה|netanya|נתניה|rehovot|רחובות|holon|חולון|beer sheva|be'er sheva|באר שבע|"
                       "yokneam|יקנעם|kfar saba|כפר סבא|hod hasharon|bnei brak|givatayim|rishon|caesarea|airport city|modiin|modi'in|ness ziona|or yehuda", re.I)
ISRAELI_BOARDS = {"alljobs", "jobmaster", "drushim", "drushimapify"}
EN_WORDS = {"the", "and", "with", "for", "you", "our", "will", "your", "are", "experience", "team", "work", "we", "of", "to", "in", "is"}
OTHER_WORDS = {"und", "der", "die", "das", "mit", "für", "wir", "sie", "et", "les", "des", "une", "pour", "nous", "vous", "avec", "el", "los",
               "las", "para", "con", "una", "che", "per", "il", "di", "een", "het", "van", "voor", "zijn", "jest", "oraz", "dla", "się", "och", "att", "är"}


def is_international(j, sc=None):
    """Assaf 21 Nov: a job is international when it is not in Israel (Israeli boards and Israeli places count as Israel)."""
    if set(j.get("boards") or []) & ISRAELI_BOARDS:
        return False
    # order matters: the job's own location beats the company's home country (Papaya is Israeli, its Barcelona job is not)
    if j.get("region") in ("IL", "EEA"):  # engine tag from the careers scan
        return j["region"] == "EEA"
    loc = str(j.get("location") or "")
    if loc.strip():
        return not IL_PLACES.search(loc)
    return ((sc or {}).get("region") or j.get("region")) in ("EEA", "Other", "Global")


def is_english_post(j):
    """Cheap check before paying to score: the post is written in English (not Hebrew, German, French, Polish...)."""
    words = re.findall(r"[a-zà-ÿąćęłńóśźż]+", f"{j.get('title', '')} {str(j.get('desc') or '')[:3000]}".lower())
    if not words:
        return True  # no text to judge -> let the review decide
    en, other = sum(w in EN_WORDS for w in words), sum(w in OTHER_WORDS for w in words)
    he = len(re.findall("[֐-׿]", str(j.get("desc") or "")[:3000]))
    return en >= other * 1.5 and he < 30


def intl_reason(j, sc, open_eu=False):
    """Assaf 21 Nov rule for jobs outside Israel: written in English, fully remote, no language required besides English and Hebrew.
    Returns why the job is out, or None when it may be shown (Israeli jobs always return None)."""
    if not is_international(j, sc):
        return None
    if not is_english_post(j):
        return "משרה בחו״ל שלא כתובה באנגלית"
    if not open_eu and (sc or {}).get("arr", j.get("arr")) != "Remote":  # Assaf's rule: outside Israel ONLY jobs proven fully remote (22 Nov: an "Unknown" fix was tried and reverted)
        return "משרה בחו״ל שאינה מרחוק מלא"
    extra = [x for x in ((sc or {}).get("langs_req", j.get("langs_req")) or []) if str(x).strip().lower() not in ("english", "hebrew", "אנגלית", "עברית")]
    if extra:
        return "משרה בחו״ל שדורשת שפה נוספת: " + ", ".join(extra)
    return None


def lang_of(j):
    s = f"{j.get('title', '')} {str(j.get('desc', ''))[:800]}"
    he, lat = len(re.findall(r"[֐-׿]", s)), len(re.findall(r"[A-Za-z]", s))
    return "he" if he > lat * 0.5 else "en"


EMP_HE = {"Full-Time": "משרה מלאה", "Part-Time": "חלקית", "Contract": "חוזה / פרויקט", "Freelance": "פרילנס", "Unknown": "לא ידוע"}
ARR_HE = {"Remote": "מרחוק", "Hybrid": "היברידי", "On-site": "מהמשרד", "Unknown": "לא ידוע"}
ELIG_HE = {"eligible": "זכאי", "needs_verification": "דורש אימות", "not_eligible": "לא מתאים לתנאים"}


def apply_prefs(ev, prefs):
    """Deterministic: the user's employment / arrangement / region choices decide eligibility on top of the model's reading."""
    prefs = prefs or {}
    notes, status = [], ev["eligibility_status"]
    emp, arr, reg = ev["employment_type"]["value"], ev["work_arrangement"]["value"], ev["location"]["job_region"]
    for val, want, he, label in [(emp, prefs.get("employment") or [], EMP_HE, "סוג העסקה"), (arr, prefs.get("arrangement") or [], ARR_HE, "אופי עבודה")]:
        if want and val != "Unknown" and val not in want:
            status = "not_eligible"
            notes.append(f"{label} {he.get(val, val)} לא בהעדפות שלך")
        elif want and val == "Unknown" and status == "eligible":
            status = "needs_verification"
            notes.append(f"{label} לא צוין, נדרשת בדיקה")
    regions = prefs.get("regions") or ["IL"]
    reg_map = {"Israel": "IL", "EEA": "EEA"}
    if reg in reg_map and reg_map[reg] not in regions and "remote" not in str(ev["location"]["allowed_work_from"]).lower():
        status = "not_eligible"
        notes.append(f"המשרה ב-{reg}, מחוץ לאזורים שבחרת")
    elif reg in ("Unknown", "Other", "Global") and status == "eligible":
        status = "needs_verification"
        notes.append("לא ברור מאיפה מותר לעבוד")
    return status, notes


def final_score(prof, status):
    """Final Match Score: the professional match, capped when the terms do not fit (a strong CV match never hides a hard conflict)."""
    prof = max(0, min(100, int(prof)))
    return min(prof, 25) if status == "not_eligible" else prof


EU_COUNTRIES = {  # EU-27, with the spellings job posts use
    "Austria": ["austria", "österreich", "vienna"], "Belgium": ["belgium", "belgië", "brussels"], "Bulgaria": ["bulgaria", "sofia"],
    "Croatia": ["croatia", "zagreb"], "Cyprus": ["cyprus", "nicosia", "limassol"], "Czechia": ["czech", "prague", "praha"],
    "Denmark": ["denmark", "copenhagen"], "Estonia": ["estonia", "tallinn"], "Finland": ["finland", "helsinki"],
    "France": ["france", "paris"], "Germany": ["germany", "deutschland", "berlin", "munich", "münchen", "hamburg"],
    "Greece": ["greece", "athens"], "Hungary": ["hungary", "budapest"], "Ireland": ["ireland", "dublin"],
    "Italy": ["italy", "italia", "milan", "rome"], "Latvia": ["latvia", "riga"], "Lithuania": ["lithuania", "vilnius"],
    "Luxembourg": ["luxembourg"], "Malta": ["malta"], "Netherlands": ["netherlands", "holland", "amsterdam", "rotterdam"],
    "Poland": ["poland", "polska", "warsaw", "krakow", "wroclaw"], "Portugal": ["portugal", "lisbon", "porto"],
    "Romania": ["romania", "bucharest"], "Slovakia": ["slovakia", "bratislava"], "Slovenia": ["slovenia", "ljubljana"],
    "Spain": ["spain", "españa", "madrid", "barcelona"], "Sweden": ["sweden", "stockholm"]}
EU_RX = {c: re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(x) for x in v) + r")(?![a-z])", re.I) for c, v in EU_COUNTRIES.items()}
LOC_ALL_EU, LOC_IL, LOC_REMOTE = "🇪🇺 כל האיחוד האירופי", "ישראל", "ללא מדינה / מרחוק בעולם"


def job_countries(j):
    """Countries named in the job's location text (EU-27 + Israel). Empty list = no country stated."""
    loc = str(j.get("location") or "")
    out = [c for c, rx in EU_RX.items() if rx.search(loc)]
    if re.search(r"israel|tel aviv|jerusalem|haifa|[֐-׿]", loc, re.I) or (not out and set(j.get("boards") or []) & {"alljobs", "jobmaster", "drushim"}):
        out.append(LOC_IL)
    return out


def loc_match(j, picks):
    cs = job_countries(j)
    for p in picks:
        if p == LOC_ALL_EU and any(c in EU_COUNTRIES for c in cs):
            return True
        if p == LOC_REMOTE and not cs:
            return True
        if p in cs:
            return True
    return False
