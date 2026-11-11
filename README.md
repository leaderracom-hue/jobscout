# Job Scout

Pick a keyword, search Israeli job boards (LinkedIn, Indeed, AllJobs, Drushim, company careers pages) on click, rank each job against the owner's profile and suggest the best-fitting CV. "Sent" / "Remove" move a job to an archive that never resurfaces.

- `app.py` — Streamlit app (password gate, search, scoring, archive).
- `engine/` — the Node search engine (`mini-agent/scout.mjs`), run as a subprocess.
- All secrets come from Streamlit secrets; all data (state + CVs) lives in a separate private repo.
