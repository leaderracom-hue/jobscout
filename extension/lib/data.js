// data.js — the private data repo (same one the Streamlit app uses): state.json, profile.json, cv/en|he/*.pdf.
// Token and repo live in chrome.storage.local on this PC only.
const GH = "https://api.github.com";

export async function settings() {
  const s = await chrome.storage.local.get(["ghToken", "ghRepo"]);
  return { token: s.ghToken || "", repo: s.ghRepo || "leaderracom-hue/jobscout-data" };
}

async function gh(path, init = {}, raw = false) {
  const { token, repo } = await settings();
  if (!token) throw new Error("חסר טוקן GitHub בהגדרות");
  const r = await fetch(`${GH}/repos/${repo}/contents/${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, Accept: raw ? "application/vnd.github.raw" : "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", ...(init.headers || {}) },
  });
  return r;
}

const b64decode = s => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/\n/g, "")), c => c.charCodeAt(0)));
const b64encode = str => { const bytes = new TextEncoder().encode(str); let bin = ""; for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000)); return btoa(bin); };

// GitHub can serve a stale read for ~60 s after a write; keep the copy we last wrote and prefer it for 90 s.
let latest = null;

export async function loadState() {
  if (latest && Date.now() - latest.t < 90000) return { s: structuredClone(latest.s), sha: latest.sha };
  const r = await gh("state.json");
  if (!r.ok) throw new Error(`GitHub ${r.status}`);
  const m = await r.json();
  const text = m.content ? b64decode(m.content) : await (await gh("state.json", {}, true)).text();
  return { s: JSON.parse(text), sha: m.sha };
}

export async function updateState(mutate, message) {
  for (let i = 0; i < 6; i++) {
    const { s, sha } = await loadState();
    mutate(s);
    const r = await gh("state.json", { method: "PUT", body: JSON.stringify({ message, sha, content: b64encode(JSON.stringify(s, null, 1)) }) });
    if (r.ok) { latest = { s, sha: (await r.json()).content.sha, t: Date.now() }; return s; }
    latest = null;
    if (![409, 422].includes(r.status)) throw new Error(`GitHub ${r.status}`);
    await new Promise(res => setTimeout(res, 1000));
  }
  throw new Error("לא הצלחתי לשמור למאגר");
}

export async function loadProfile() {
  const r = await gh("profile.json", {}, true);
  if (!r.ok) throw new Error(`profile.json: GitHub ${r.status}`);
  return r.json();
}

export async function loadCv(lang, name) { // → base64 of the PDF, for the form's file input
  const r = await gh(`cv/${lang}/${encodeURIComponent(name)}`, {}, true);
  if (!r.ok) throw new Error(`קו"ח ${name}: GitHub ${r.status}`);
  const buf = new Uint8Array(await r.arrayBuffer());
  let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return btoa(bin);
}
