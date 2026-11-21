"""Pulls the cover letters saved by the app (cover-letter/ in the private data repo) into
C:\\Users\\Windows\\Desktop\\Resume\\Cover Letter. Reads the token by name from .streamlit/secrets.toml. Existing files are not overwritten."""
import base64
import pathlib
import tomllib

import requests

HERE = pathlib.Path(__file__).parent
DEST = pathlib.Path(r"C:\Users\Windows\Desktop\Resume\Cover Letter")
sec = tomllib.loads((HERE / ".streamlit" / "secrets.toml").read_text(encoding="utf-8"))
H = {"Authorization": "Bearer " + sec["GH_DATA_TOKEN"], "Accept": "application/vnd.github+json"}
base = f"https://api.github.com/repos/{sec['GH_DATA_REPO']}/contents/cover-letter"
r = requests.get(base, headers=H, timeout=30)
if r.status_code == 404:
    print("No cover letters saved yet.")
    raise SystemExit
r.raise_for_status()
DEST.mkdir(parents=True, exist_ok=True)
new = 0
for f in r.json():
    if not f["name"].lower().endswith(".pdf"):
        continue
    out = DEST / f["name"]
    if out.exists():
        continue
    raw = requests.get(f["url"], headers={**H, "Accept": "application/vnd.github.raw+json"}, timeout=60)
    raw.raise_for_status()
    out.write_bytes(raw.content)
    new += 1
    print("saved", out.name)
print(f"Done. {new} new file(s) in {DEST}")
