"""Connect every CV in Desktop\\Resume\\English and \\Hebrew to Job Scout (run Desktop\\Sync-CVs.bat after adding or changing a CV).

1. Uploads new or changed PDFs to the private data repo (cv/en, cv/he). Never deletes anything.
2. Profiles every CV that is new or changed with Claude Haiku (family, target roles, ATS keywords, proof points, choose_when, weak_for)
   and writes them into cv/brain.json, which the engine reads when it picks the CV for each job.
Reads ANTHROPIC_API_KEY, GH_DATA_TOKEN and GH_DATA_REPO by name from .streamlit/secrets.toml. Cost: about $0.005 per new CV."""
import base64
import hashlib
import json
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent.parent
RESUME = Path(r"C:\Users\Windows\Desktop\Resume")
FOLDERS = {"en": RESUME / "English", "he": RESUME / "Hebrew"}
SKIP = {"Assaf Blitz CC and Admin HE V1.pdf"}
sec = tomllib.loads((HERE / ".streamlit" / "secrets.toml").read_text(encoding="utf-8"))
H = {"Authorization": "Bearer " + sec["GH_DATA_TOKEN"], "Accept": "application/vnd.github+json"}
API = f"https://api.github.com/repos/{sec['GH_DATA_REPO']}/contents/"

SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["family", "positioning", "target_roles", "ats_keywords", "proof_points", "key_tools", "seniority_angle", "choose_when", "weak_for"],
          "properties": {
              "family": {"type": "string", "enum": ["product", "ai-product", "gtm-ai-builder", "marketing-automation", "crm-revops", "project-delivery",
                                                    "technical-pm", "customer-success-implementation", "presales-solutions", "account-management",
                                                    "sales", "support-admin", "data-bi", "growth-pmm", "ai-trainer", "other"]},
              "positioning": {"type": "string"}, "target_roles": {"type": "array", "items": {"type": "string"}},
              "ats_keywords": {"type": "array", "items": {"type": "string"}}, "proof_points": {"type": "array", "items": {"type": "string"}},
              "key_tools": {"type": "array", "items": {"type": "string"}}, "seniority_angle": {"type": "string"},
              "choose_when": {"type": "string"}, "weak_for": {"type": "string"}}}
PROMPT = """Below is one of the candidate's CVs (file: {f}). Profile it so a matching engine can choose the right CV for a job post. Use ONLY what is written in the CV.
- family: the single best category.
- positioning: the headline angle of this CV, max 20 words, English.
- target_roles: 4-8 job titles this CV is built to win (English; add the Hebrew title when the CV is Hebrew).
- ats_keywords: the 10-14 most distinctive keywords/skills this CV emphasizes (what an ATS would match on).
- proof_points: 3-5 strongest concrete claims in the CV (with their numbers), each max 18 words.
- key_tools: up to 10 tools / platforms named.
- seniority_angle: one phrase, e.g. "hands-on builder, no people management", "project/delivery lead".
- choose_when: one sentence in English: the kind of job post where this CV should be chosen over the others.
- weak_for: one sentence: job types where this CV is a weak choice.
Be specific to THIS CV.

=== CV TEXT ===
{t}"""


def blob_sha(b):
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def _req(method, url, **kw):
    """GitHub sometimes drops a connection or answers 5xx: retry a few times before giving up."""
    import time
    for i in range(5):
        try:
            r = requests.request(method, url, timeout=90, **kw)
            if r.status_code < 500:
                return r
        except requests.RequestException:
            if i == 4:
                raise
        time.sleep(2 * (i + 1))
    return r


def remote(path):
    r = _req("GET", API + requests.utils.quote(path), headers=H)
    return r.json() if r.status_code == 200 else None


def put(path, data, msg):
    old = remote(path)
    if old and old.get("sha") == blob_sha(data):
        return False
    body = {"message": msg, "content": base64.b64encode(data).decode()}
    if old:
        body["sha"] = old["sha"]
    _req("PUT", API + requests.utils.quote(path), headers=H, json=body).raise_for_status()
    return True


def pdf_text(path):
    import pypdf
    return "\n".join((pg.extract_text() or "") for pg in pypdf.PdfReader(str(path)).pages).strip()


def profile(item):
    import anthropic
    key, path = item
    text = pdf_text(path)
    if len(text) < 800:
        return key, None
    client = anthropic.Anthropic(api_key=sec["ANTHROPIC_API_KEY"], max_retries=3)
    r = client.messages.create(model="claude-haiku-5-5", max_tokens=6000, output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                               messages=[{"role": "user", "content": PROMPT.format(f=path.name, t=text[:9000])}])
    t = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
    if r.stop_reason == "max_tokens" or not t:
        raise RuntimeError(f"{path.name}: reply cut or empty")
    d = json.loads(t)
    d.update({"file": path.name, "lang": key.split("/")[0], "near_duplicates": [], "closest": [], "sha": blob_sha(path.read_bytes())})
    return key, d


def main():
    meta = remote("cv/brain.json")
    brain = json.loads(base64.b64decode(meta["content"]).decode("utf-8")) if meta and meta.get("content") else {"rules": "", "profiles": {}}
    if meta and not meta.get("content"):  # over 1 MB: fetch raw
        raw = _req("GET", API + "cv/brain.json", headers={**H, "Accept": "application/vnd.github.raw+json"})
        brain = raw.json()
    uploaded, todo = [], []
    for lang, folder in FOLDERS.items():
        for f in sorted(folder.glob("*.pdf")):
            if f.name in SKIP:
                continue
            key = f"{lang}/{f.name}"
            if put(f"cv/{lang}/{f.name}", f.read_bytes(), f"CV sync: {f.name}"):
                uploaded.append(key)
            prof = brain["profiles"].get(key)
            if not prof or (prof.get("sha") and prof["sha"] != blob_sha(f.read_bytes())) or key in uploaded and prof and not prof.get("sha"):
                todo.append((key, f))
    print(f"uploaded {len(uploaded)} PDF(s); profiling {len(todo)} CV(s)")
    with ThreadPoolExecutor(4) as ex:
        for key, d in ex.map(profile, todo):
            if d:
                brain["profiles"][key] = d
                print(f"  {key}: {d['family']} - {d['choose_when'][:90]}")
    if todo:
        put("cv/brain.json", json.dumps(brain, ensure_ascii=False).encode("utf-8"), f"CV brain: {len(todo)} profile(s)")
    local = HERE.parent / "_cvwork" / "brain_final.json"  # local copy for the Claude skill that picks a CV for a pasted job
    local.parent.mkdir(exist_ok=True)
    local.write_text(json.dumps(brain, ensure_ascii=False), encoding="utf-8")
    print(f"done. {len(brain['profiles'])} CVs in the brain. The app picks the change up within 10 minutes.")


if __name__ == "__main__":
    sys.exit(main())
