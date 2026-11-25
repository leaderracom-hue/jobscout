"""The job seeker's own facts, read from profile.json (one per user) instead of being written into the code.
Every prompt, the CV PDF and the screens take the candidate's name, headline, eligibility and contact lines from here.
A missing field falls back to a neutral wording, never to another person's facts."""


def first_name(p):
    return (p or {}).get("first_name") or "the candidate"


def headline(p):
    return (p or {}).get("headline") or "job seeker"


def persona(p):
    """'Assaf (AI and marketing-automation product manager; ...)' for writing prompts."""
    return f"{first_name(p)} ({headline(p)})"


def eligibility_facts(p):
    """One line for the scoring rubric: where the candidate lives and where he may work."""
    p = p or {}
    place = ", ".join(x for x in (p.get("city"), p.get("country")) if x) or "not stated"
    parts = [f"Candidate eligibility facts: lives in {place}."]
    if p.get("citizenship"):
        parts.append(f"Citizenship: {p['citizenship']}.")
    if str(p.get("authorized_eu") or "").lower().startswith("yes"):
        parts.append("May work anywhere in the EU/EEA without sponsorship.")
    if p.get("needs_sponsorship"):
        parts.append(f"Needs visa sponsorship: {p['needs_sponsorship']}.")
    parts.append('For a remote job run from abroad, check whether the employer hires people based where the candidate lives '
                 '(or via contractor/EOR); "Remote Europe" alone does not prove that.')
    return " ".join(parts)


def eu_citizen(p):
    return str((p or {}).get("authorized_eu") or "").lower().startswith("yes")


def cv_contact(p):
    """Contact lines in the CV sidebar."""
    p = p or {}
    li = str(p.get("linkedin") or "").replace("https://", "").replace("http://", "").replace("www.", "")
    phone = p.get("phone_display") or p.get("phone")
    lines = [p.get("email"), li, f"WhatsApp {phone}" if phone else None,
             ", ".join(x for x in (p.get("city"), p.get("country")) if x) or None, p.get("citizenship_short")]
    return [x for x in lines if x]


def cv_languages(p):
    langs = (p or {}).get("languages_list")
    return list(langs) if langs else []


def default_keywords(p):
    return ", ".join((p or {}).get("default_search_words") or [])
