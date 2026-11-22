"""Pulls what the app saved in the private data repo to the PC (existing files are never overwritten):
  cover-letter/*.pdf -> C:\\Users\\Windows\\Desktop\\Resume\\Cover Letter
  resume/en/*.pdf    -> C:\\Users\\Windows\\Desktop\\Resume\\English
  resume/he/*.pdf    -> C:\\Users\\Windows\\Desktop\\Resume\\Hebrew
Reads the token by name from .streamlit/secrets.toml."""
import pathlib
import tomllib

import requests

HERE = pathlib.Path(__file__).parent
ROOT = pathlib.Path(r"C:\Users\Windows\Desktop\Resume")
MAP = {"cover-letter": ROOT / "Cover Letter", "resume/en": ROOT / "English", "resume/he": ROOT / "Hebrew"}
sec = tomllib.loads((HERE / ".streamlit" / "secrets.toml").read_text(encoding="utf-8"))
H = {"Authorization": "Bearer " + sec["GH_DATA_TOKEN"], "Accept": "application/vnd.github+json"}
total = 0
for src, dest in MAP.items():
    r = requests.get(f"https://api.github.com/repos/{sec['GH_DATA_REPO']}/contents/{src}", headers=H, timeout=30)
    if r.status_code == 404:
        continue
    r.raise_for_status()
    dest.mkdir(parents=True, exist_ok=True)
    for f in r.json():
        if not f["name"].lower().endswith(".pdf") or (dest / f["name"]).exists():
            continue
        raw = requests.get(f["url"], headers={**H, "Accept": "application/vnd.github.raw+json"}, timeout=60)
        raw.raise_for_status()
        (dest / f["name"]).write_bytes(raw.content)
        total += 1
        print("saved", dest.name, "/", f["name"])
print(f"Done. {total} new file(s).")
