// background.js — opens a queued job's application page, fills it (content/fill.js, every frame), watches for the
// "thank you" page and records the job as sent. It NEVER clicks Submit: Assaf does.
import { loadState, updateState, loadProfile, loadCv } from "./lib/data.js";

const DAILY_CAP = 15;
const tracked = new Map(); // tabId → { key, filled, sent }

chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

const norm = s => String(s || "").toLowerCase().replace(/[^a-z֐-׿0-9]/g, "");
const notify = () => chrome.runtime.sendMessage({ type: "changed" }).catch(() => {});

// which page to open for a job: the company's own application form when we know it
export function door(j) {
  const links = { ...(j.links || {}) };
  const urls = [links.apply, links.careers, ...Object.values(links), j.url].filter(Boolean);
  for (const u of urls) {
    if (/greenhouse\.io/.test(u)) return { kind: "greenhouse", url: u };
    if (/jobs\.(eu\.)?lever\.co\//.test(u)) return { kind: "lever", url: u.replace(/\/(apply)?\/?(\?.*)?$/, "") + "/apply" };
    if (/jobs\.ashbyhq\.com\//.test(u)) return { kind: "ashby", url: u.replace(/\/(application)?\/?(\?.*)?$/, "") + "/application" };
    if (/comeet\.(com|co)\/jobs\//.test(u)) return { kind: "comeet", url: u };
  }
  if (links.careers) return { kind: "careers-site", url: links.careers };
  if (links.apply) return { kind: "company-site", url: links.apply };
  return null; // LinkedIn / Drushim / AllJobs only → phases 3–4
}

async function setQueue(key, status, note = "") {
  await updateState(s => { (s.queue ||= {})[key] = { ...(s.queue[key] || {}), status, note, at: new Date().toISOString() }; }, `extension ${status}: ${key}`);
  notify();
}

async function openAndFill(key) {
  const { s } = await loadState();
  const j = s.jobs[key];
  if (!j) throw new Error("המשרה לא נמצאה במאגר");
  const today = new Date().toISOString().slice(0, 10);
  const sentToday = Object.values(s.archive).filter(a => a.status === "sent" && ["agent", "extension"].includes(a.by) && String(a.at).startsWith(today)).length;
  if (sentToday >= DAILY_CAP) throw new Error(`הגעת לתקרה היומית (${DAILY_CAP} הגשות)`);
  const since = Date.now() - 30 * 864e5;
  const company = norm(j.company);
  if (company && company !== norm("(חסוי)") && Object.values(s.archive).some(a => a.status === "sent" && norm(a.company) === company && new Date(a.at) > since))
    throw new Error("כבר נשלחה הגשה לחברה הזו ב-30 הימים האחרונים");
  const d = door(j);
  if (!d) { await setQueue(key, "unsupported", "אין דף הגשה של החברה. LinkedIn / דרושים / AllJobs בשלבים הבאים"); throw new Error("אין דף הגשה של החברה עדיין"); }
  const [profile, cvB64] = await Promise.all([loadProfile(), j.cv ? loadCv(j.lang === "he" ? "he" : "en", j.cv) : Promise.resolve(null)]);
  const tab = await chrome.tabs.create({ url: d.url, active: true });
  tracked.set(tab.id, { key, filled: false, sent: false, payload: { profile, cv: cvB64 ? { name: j.cv, b64: cvB64 } : null, lang: j.lang || "en", job: { title: j.title, company: j.company } } });
  return { kind: d.kind, url: d.url };
}

async function fillTab(tabId) {
  const t = tracked.get(tabId); if (!t || t.filled) return;
  t.filled = true;
  await new Promise(r => setTimeout(r, 2000)); // let the page's own scripts build the form
  await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ["content/fill.js"] });
  const results = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: p => window.JobScoutFill(p), args: [t.payload] });
  const done = results.flatMap(r => r.result?.done || []), needs = results.flatMap(r => r.result?.needs || []);
  t.result = { done, needs };
  if (!done.length) await setQueue(t.key, "needs_you", "לא מצאתי טופס בדף. מלא ידנית או דלג");
  else await setQueue(t.key, "ready", needs.length ? `חסר: ${needs.slice(0, 4).join(" · ")}` : "הכל מולא. בדוק ולחץ שלח");
}

async function watch(tabId) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["content/watch.js"] }).catch(() => {});
}

async function markSent(key, by = "extension") {
  const now = new Date().toISOString();
  await updateState(s => {
    const j = s.jobs[key] || {};
    s.archive[key] = { status: "sent", by, at: now, title: j.title, company: j.company, urls: Object.values(j.links || { x: j.url }) };
    if (s.queue) delete s.queue[key];
  }, `${by} sent: ${key}`);
  notify();
}

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status !== "complete" || !tracked.has(tabId)) return;
  const t = tracked.get(tabId);
  if (!t.filled) fillTab(tabId).then(() => watch(tabId)).catch(e => setQueue(t.key, "failed", String(e.message).slice(0, 120)));
  else watch(tabId); // after a navigation (often the thank-you page)
});

chrome.tabs.onRemoved.addListener(tabId => {
  const t = tracked.get(tabId); if (!t) return;
  tracked.delete(tabId);
  if (!t.sent) setQueue(t.key, "skipped", "נסגר בלי שליחה").catch(() => {});
});

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    if (msg.type === "confirmed" && sender.tab && tracked.has(sender.tab.id)) {
      const t = tracked.get(sender.tab.id);
      if (!t.sent) { t.sent = true; await markSent(t.key); }
      return { ok: true };
    }
    if (msg.type === "queue") {
      const { s } = await loadState();
      const q = s.queue || {};
      const jobs = Object.entries(q).filter(([k]) => !s.archive[k] && s.jobs[k]).map(([k, v]) => ({ ...s.jobs[k], key: k, q: v, door: door(s.jobs[k])?.kind || null }));
      const today = new Date().toISOString().slice(0, 10);
      const sentToday = Object.values(s.archive).filter(a => a.status === "sent" && ["agent", "extension"].includes(a.by) && String(a.at).startsWith(today)).length;
      return { jobs, sentToday, cap: DAILY_CAP };
    }
    if (msg.type === "open") return await openAndFill(msg.key);
    if (msg.type === "remove") { await updateState(s => { if (s.queue) delete s.queue[msg.key]; }, `extension remove from queue: ${msg.key}`); notify(); return { ok: true }; }
    if (msg.type === "sentByHand") { await markSent(msg.key, "assaf"); return { ok: true }; }
    return { error: "unknown message" };
  })().then(reply, e => reply({ error: String(e.message || e) }));
  return true; // async reply
});
