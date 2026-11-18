# Job Scout — Chrome extension (phase 1)

Shows the Job Scout send queue in Chrome's side panel. "פתח ומלא" opens the company's application page, fills it from the answer bank, uploads the chosen CV and marks in orange what it left for you. **You click Submit.** When the site shows its thank-you page, the job moves to the archive as sent.

## Install (once)
1. Chrome → `chrome://extensions` → turn on **Developer mode** (top right).
2. **Load unpacked** → choose the folder `C:\Users\Windows\Desktop\Cluade Dev\job-scout-cloud\extension`.
3. Pin it (puzzle icon → pin **Job Scout**), click it → the side panel opens.
4. **הגדרות** → paste the same GitHub token you put in Streamlit (`GH_DATA_TOKEN`) → **שמור**.

## Use
- In jobscoutsearch.streamlit.app click **לשליחה ←** on jobs → they appear in the panel (רענן).
- **פתח ומלא** → a new tab opens and fills. Check it, fill the orange fields, click Submit.
- Closing the tab without submitting marks the job "נסגר בלי שליחה"; you can try again.

## What phase 1 covers
Comeet, Greenhouse, Lever, Ashby, and company sites that LinkedIn/Indeed link to. LinkedIn Easy Apply, Drushim and AllJobs come in phases 3–4. Cover letters and free-text answers (Claude) come in phase 2; until then those fields are orange.

## Rules
Never submits, never types passwords, never ticks consent boxes, never fills salary/EEO/ID. Max 15 sent a day, one application per company per 30 days. The token is stored only in this browser (`chrome.storage.local`).
