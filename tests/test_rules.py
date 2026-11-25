"""Phase 1: the engine rules and the score math, tested without Streamlit or the network.  Run: python -m pytest -q"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import profile as PF  # noqa: E402
from core.rules import (  # noqa: E402
    LOC_ALL_EU, LOC_IL, LOC_REMOTE, apply_prefs, company_key, excluded, final_score, intl_reason, is_cyber, is_english_post,
    is_international, is_medical, job_countries, key_of, lang_of, loc_match)


def ev(emp="Full-Time", arr="Remote", region="Israel", status="eligible", allowed="Israel"):
    return {"employment_type": {"value": emp}, "work_arrangement": {"value": arr}, "location": {"job_region": region, "allowed_work_from": allowed},
            "eligibility_status": status}


def test_key_ignores_case_and_punctuation():
    assert key_of({"company": "Wix.com Ltd", "title": "Senior PM!"}) == key_of({"company": "wix.com ltd", "title": "senior pm"})


def test_company_key_hides_confidential():
    assert company_key("חסוי") == "" and company_key("Confidential") == "" and company_key("Monday") == "monday"


def test_cyber_and_medical_blocked():
    assert is_cyber({"title": "Cyber Security PM", "company": "X"})
    assert is_medical({"title": "Product Manager - Medical Devices", "company": "X"})
    assert not is_medical({"title": "Marketing Automation Manager", "company": "Wix"})


def test_excluded_uses_user_words_and_blocked_companies():
    s = {"settings": {"exclude_words": ["intern"], "keep_words": [], "blocked_companies": ["acme"], "followed_companies": []}}
    assert excluded(s, {"title": "Product Intern", "company": "Y"}).startswith("מילה")
    assert excluded(s, {"title": "PM", "company": "Acme"}).startswith("חברה חסומה")
    assert excluded(s, {"title": "Internal Tools PM", "company": "Y"}) is None  # whole word only
    assert excluded(s, {"title": "GTM Intern", "company": "Y"}) is None  # GTM wins over exclude words


def test_israel_vs_abroad():
    assert not is_international({"location": "Tel Aviv, Israel", "boards": ["linkedin"]})
    assert not is_international({"location": "", "boards": ["alljobs"]})
    assert is_international({"location": "Berlin, Germany", "boards": ["linkedin"]})


def test_language_checks():
    assert is_english_post({"title": "Product Manager", "desc": "You will work with the team and our customers on the roadmap."})
    assert not is_english_post({"title": "Produktmanager", "desc": "Wir suchen und die Rolle mit der Erfahrung für uns und sie."})
    assert lang_of({"title": "מנהל מוצר", "desc": "דרוש מנהל מוצר לחברת הייטק בתל אביב"}) == "he"


def test_abroad_rule_and_open_europe():
    j = {"location": "Berlin, Germany", "boards": ["linkedin"], "title": "PM", "desc": "You will work with the team and our customers."}
    assert intl_reason(j, {"arr": "Hybrid", "langs_req": []}) == "משרה בחו״ל שאינה מרחוק מלא"
    assert intl_reason(j, {"arr": "Hybrid", "langs_req": []}, open_eu=True) is None
    assert "German" in intl_reason(j, {"arr": "Remote", "langs_req": ["English", "German"]})


def test_countries_and_location_filter():
    assert job_countries({"location": "Warsaw, Poland"}) == ["Poland"]
    assert LOC_IL in job_countries({"location": "Tel Aviv"})
    assert loc_match({"location": "Madrid"}, [LOC_ALL_EU]) and loc_match({"location": ""}, [LOC_REMOTE])


def test_prefs_make_hard_conflicts_not_eligible():
    assert apply_prefs(ev(arr="On-site"), {"arrangement": ["Remote"]})[0] == "not_eligible"
    assert apply_prefs(ev(arr="Unknown"), {"arrangement": ["Remote"]})[0] == "needs_verification"
    assert apply_prefs(ev(region="EEA", allowed="Berlin office"), {"regions": ["IL"]})[0] == "not_eligible"
    assert apply_prefs(ev(region="EEA", allowed="Remote, EU"), {"regions": ["IL"]})[0] == "eligible"


def test_final_score_caps_not_eligible():
    assert final_score(90, "not_eligible") == 25 and final_score(90, "eligible") == 90 and final_score(140, "eligible") == 100


def test_profile_has_no_built_in_person():
    assert PF.persona({}) == "the candidate (job seeker)"
    assert PF.cv_contact({}) == [] and PF.cv_languages({}) == []
    facts = PF.eligibility_facts({"city": "Warsaw", "country": "Poland", "authorized_eu": "Yes"})
    assert "Warsaw, Poland" in facts and "EU/EEA" in facts
