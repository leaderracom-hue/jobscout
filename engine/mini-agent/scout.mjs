// scout.mjs — Israel job scout: one keyword + location -> LinkedIn, Indeed, Drushim, AllJobs (+ JobMaster) via Apify -> one filtered list.
// Every live run spends Apify money: run it only with Assaf's OK for that run. --dry and --replay spend nothing.
//
//   node mini-agent/scout.mjs "marketing automation" [--loc all|tlv|center|north|south|jerusalem|remote] [--days 1|3|7|14]
//        [--boards linkedin,indeed,drushim,alljobs,careers,jobmaster,drushimapify] [--free] [--rows 30] [--he "אוטומציה שיווקית"] [--all-titles] [--match] [--dry]
//   drushim + careers are FREE (plain HTTP: Drushim search pages, Comeet/Greenhouse/Lever/Ashby boards from ats-companies.json).
//   --free = only the free sources ($0, no Apify). drushimapify = the old paid Drushim actor, kept as a fallback.
//   node mini-agent/scout.mjs "marketing automation" --replay runs/il-boards-test-2026-11-11     (re-filter saved raw files, $0)
//
// Output: runs/scout/<stamp>-<slug>/  raw/<board>.json · results.json · results.csv (Excel-ready, Hebrew safe)
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { env, RUNS_DIR } from "../env.mjs";
import { drushim as drushimFree, careers as careersFree } from "./sources-free.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DICT = JSON.parse(fs.readFileSync(path.join(HERE, "dictionary.json"), "utf8"));

// ---------- args ----------
const argv = process.argv.slice(2);
const flag = (n, d) => { const i = argv.indexOf("--" + n); return i < 0 ? d : (argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[i + 1] : true); };
const KW = argv.find(a => !a.startsWith("--") && argv[argv.indexOf(a) - 1]?.startsWith("--") !== true) || argv[0];
if (!KW || KW.startsWith("--")) { console.log('usage: node mini-agent/scout.mjs "marketing automation" [--loc center] [--days 7] [--dry]'); process.exit(1); }
const kwKey = KW.trim().toLowerCase();
const SEARCH = (JSON.parse(fs.readFileSync(path.join(HERE, "dictionary.json"), "utf8"))[kwKey] || {}).en || KW; // what the boards get ("product ai" → "AI product manager")
const entry = DICT[kwKey] || {};
const HE = flag("he", entry.he || "");
const LOC = flag("loc", "all");
const DAYS = +flag("days", 7);
const ROWS = +flag("rows", 30);
const BOARDS = String(flag("free", false) ? "drushim,careers" : flag("boards", "linkedin,indeed,drushim,alljobs,careers")).split(",").map(s => s.trim()).filter(Boolean);
const DRY = !!flag("dry", false), MATCH = !!flag("match", false), ALL_TITLES = !!flag("all-titles", false);
const REPLAY = flag("replay", "");

// ---------- actors ----------
const PUB = d => d <= 1 ? "r86400" : d <= 7 ? "r604800" : "r2592000";
const NEAR = d => String([1, 3, 7, 14].find(x => x >= d) || 14);
const half = Math.ceil(ROWS / 2);
const ACTORS = {
  linkedin: { id: "bebity/linkedin-jobs-scraper", perItem: 0.0015, runs: () => [{ titles: [SEARCH], locations: ["Israel"], rows: ROWS, publishedAt: PUB(DAYS) }] },
  indeed:   { id: "valig/indeed-jobs-scraper", perItem: 0.0001, start: 0.001, runs: () => [{ country: "il", title: SEARCH, limit: ROWS, datePosted: NEAR(DAYS) }] },
  drushimapify: { id: "blackfalcondata/drushim-scraper", perItem: 0.002, start: 0.01, runs: () => [{ query: SEARCH, maxResults: HE ? half : ROWS, maxPages: 3 }, ...(HE ? [{ query: HE, maxResults: half, maxPages: 3 }] : [])] },
  alljobs:  { id: "getascraper/alljobs-scraper", perItem: 0.0018, runs: () => [{ keywords: [SEARCH, HE].filter(Boolean), maxItems: ROWS, postedWithinDays: DAYS, detailMode: "always" }] },
  jobmaster:{ id: "getascraper/jobmaster-scraper", perItem: 0.0015, runs: () => [{ searchQueries: [SEARCH, HE].filter(Boolean), maxItems: ROWS, maxItemsPerQuery: HE ? half : ROWS, sortBy: "newest" }] },
};
const FREE = {
  drushim: () => drushimFree([SEARCH, HE], { pages: 2 }),
  careers: () => careersFree(),
};
for (const b of BOARDS) if (!ACTORS[b] && !FREE[b]) { console.log("unknown board:", b, "— use", [...Object.keys(ACTORS), ...Object.keys(FREE)].join(",")); process.exit(1); }
const PAID = BOARDS.filter(b => ACTORS[b]), FREEB = BOARDS.filter(b => FREE[b]);
const estimate = () => PAID.reduce((s, b) => { const a = ACTORS[b]; const n = a.runs().length; return s + n * (a.start || 0) + ROWS * a.perItem; }, 0);

// ---------- normalize ----------
const iso = v => { if (!v) return ""; if (/^\d{4}-\d{2}-\d{2}$/.test(String(v))) return v + "T21:59:59.000Z"; const d = new Date(v); return isNaN(d) ? "" : d.toISOString(); }; // full time; a bare date = end of that Israeli day ("dayOnly")
const relDays = s => { // "פורסם לפני 4 שעות" / "לפני 2 ימים" / "5 hours ago" -> ISO time
  if (!s) return ""; const m = String(s).match(/(\d+)\s*(דקות|דקה|שעות|שעה|ימים|יום|minutes?|hours?|days?|weeks?)/); if (!m) return "";
  const n = +m[1], unit = /דק|minute/.test(m[2]) ? 6e4 : /שע|hour/.test(m[2]) ? 36e5 : /week/.test(m[2]) ? 6048e5 : 864e5;
  return new Date(Date.now() - n * unit).toISOString();
};
const NORM = {
  linkedin: j => ({ title: j.title, company: j.companyName, location: j.location, posted: relDays(j.postedTime) || iso(j.publishedAt), url: j.jobUrl, easyApply: j.applyType === "EASY_APPLY", poster: j.posterFullName || "", applicants: j.applicationsCount || "", desc: j.description }),
  indeed:   j => ({ title: j.title, company: j.employer?.name, location: j.location?.city || j.location?.formatted?.long || "", posted: iso(j.datePublished), url: j.url, desc: j.description?.text || "" }),
  drushimapify: j => ({ title: j.title, company: j.company, location: j.location || j.locationEnglish, posted: iso(j.postedAt || j.postedDate), url: j.portalUrl || j.applyUrl, desc: [j.description, j.requirements].filter(Boolean).join("\n") }),
  drushim:  j => j.jobCode ? NORM.drushimapify(j) : j,   // free fetcher already normalized; old Apify raw files still replay
  careers:  j => j,
  alljobs:  j => { // detailMode "always" fills companyName; on confidential posts it sometimes carries the breadcrumb/title instead
    const crumb = s => String(s || "").replace(/^.*»\s*/, "").trim();
    const title = crumb(j.title), co = String(j.companyName || j.company || "");
    const company = j.isConfidentialCompany || co.includes("»") || crumb(co) === title ? "(חסוי)" : co;
    return { title, company, location: j.location, posted: iso(j.postedAt), url: j.url, desc: [j.description, j.requirements].filter(Boolean).join("\n") }; },
  jobmaster:j => ({ title: j.title, company: j.companyName, location: j.location, posted: relDays(j.postedRelative), url: j.url, desc: [j.descriptionText, j.requirementsText].filter(Boolean).join("\n") }),
};

// ---------- filters ----------
const REGIONS = {
  tlv: /tel.?aviv|תל.?אביב|יפו/i,
  center: /tel.?aviv|תל.?אביב|יפו|petah|petach|פתח.?תקו|herzl|הרצלי|ramat.?gan|רמת.?גן|givat|גבעתיים|holon|חולון|bat.?yam|בת.?ים|rishon|ראשון|rehovot|רחובות|ness.?ziona|נס.?ציונה|ra.?anana|רעננה|kfar.?saba|כפר.?סבא|hod.?hasharon|הוד.?השרון|netanya|נתניה|bnei.?brak|בני.?ברק|airport|איירפורט|lod|לוד|modi.?in|מודיעין|ramla|רמלה|yavne|יבנה|rosh.?ha.?ayin|ראש.?העין|or.?yehuda|אור.?יהודה|kiryat.?ono|קרית.?אונו|center|מרכז|shoham|שוהם|be.?er.?yaakov|באר.?יעקב/i,
  north: /haifa|חיפה|north|צפון|yokneam|יקנעם|nazareth|נצרת|karmiel|כרמיאל|akko|acre|עכו|nahariya|נהריה|caesarea|קיסריה|hadera|חדרה|tiberias|טבריה|migdal|מגדל.?העמק|afula|עפולה|harish|חריש|kiryat.?(ata|bialik|motzkin|yam)|קרית.?(אתא|ביאליק|מוצקין|ים)/i,
  south: /be.?er.?sheva|באר.?שבע|ashdod|אשדוד|ashkelon|אשקלון|eilat|אילת|south|דרום|kiryat.?gat|קרית.?גת|dimona|דימונה/i,
  jerusalem: /jerusalem|ירושלים|beit.?shemesh|בית.?שמש|mevaseret|מבשרת/i,
  remote: /remote|מהבית|היברידי|hybrid|from home/i,
};
const vague = l => !l || /^\s*(israel|ישראל|—|-)?\s*$/i.test(l);
const locOk = j => LOC === "all" || vague(j.location) || REGIONS[LOC]?.test(j.location) || (LOC === "remote" && REGIONS.remote.test(j.desc || ""));
const daysOk = j => !j.posted || (Date.now() - new Date(j.posted)) / 36e5 <= DAYS * 24 + 1;
const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\//g, "\\/?").replace(/\s+/g, "[\\s/-]*");
const rx = s => new RegExp(/^[\x00-\x7f]+$/.test(s) ? `\\b${esc(s)}\\b` : esc(s), "i"); // Latin terms on word edges ("unica" must not hit "Communications")
const includes = [KW, SEARCH, HE, ...(entry.include || [])].filter(Boolean).map(rx);
const requireAlso = (entry.requireAlso || []).map(rx);
const kwWords = kwKey.split(/\s+/).filter(w => w.length > 2);
const EXCLUDE = /\bqa\b|בודק|בדיקות|לוגיסט|תעשייתי|מכשור|בקרה|הדרכה|נהג|מחסן|טכנאי|qa automation|test automation/i;
const EXCLUDE_DEV = /מפתח|פיתוח|developer|engineer/i;           // off for keywords that want builders (allowDev)
const exclusionApplies = !entry.noExclude && !EXCLUDE.test(KW);
function titleVerdict(t) {
  t = String(t || "");
  if (exclusionApplies && EXCLUDE.test(t)) return "out";
  if (exclusionApplies && !entry.allowDev && EXCLUDE_DEV.test(t)) return "out";
  if (requireAlso.length && !requireAlso.some(r => r.test(t))) return "out";
  if (includes.some(r => r.test(t))) return "in";
  if (kwWords.length > 1 && kwWords.every(w => t.toLowerCase().includes(w))) return "in";
  return "out";
}
const normKey = j => (String(j.company || "").toLowerCase().replace(/[^a-z֐-׿0-9]/g, "").slice(0, 10)) + "|" + String(j.title || "").toLowerCase().replace(/[^a-z֐-׿0-9]/g, "").slice(0, 24);

// ---------- apify ----------
const T = env.APIFY_API_TOKEN;
async function apify(pathname, init) { const r = await fetch("https://api.apify.com/v2" + pathname + (pathname.includes("?") ? "&" : "?") + "token=" + T, init); return r.json(); }
async function runActor(id, input) {
  const start = await apify(`/acts/${id.replace("/", "~")}/runs?waitForFinish=60&maxTotalChargeUsd=0.25`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(input) });
  let run = start.data; if (!run) throw new Error("start failed: " + JSON.stringify(start.error || start).slice(0, 200));
  const t0 = Date.now();
  while (!["SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"].includes(run.status) && Date.now() - t0 < 420000) run = (await apify(`/actor-runs/${run.id}?waitForFinish=60`)).data;
  const items = await apify(`/datasets/${run.defaultDatasetId}/items?clean=1`);
  return { status: run.status, items: Array.isArray(items) ? items : [] };
}

// ---------- main ----------
const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
const OUT = flag("out", "") ? path.resolve(flag("out")) : path.join(RUNS_DIR, "scout", `${stamp}-${kwKey.replace(/[^a-z0-9א-ת]+/g, "-")}${REPLAY ? "-replay" : ""}`);
console.log(`\nscout: "${KW}"${HE ? ` + "${HE}"` : ""} · loc ${LOC} · ${DAYS} days · boards ${BOARDS.join(", ")} · ${ROWS} per board`);
console.log(`estimated Apify cost: $${estimate().toFixed(3)}${REPLAY ? " (replay: $0)" : ""}`);
if (DRY) { for (const b of PAID) console.log(" ", b.padEnd(9), ACTORS[b].id, JSON.stringify(ACTORS[b].runs())); for (const b of FREEB) console.log(" ", b.padEnd(9), "free (plain HTTP)"); process.exit(0); }

const raw = {}; const runLog = [];
if (REPLAY) {
  const dir = path.resolve(REPLAY);
  for (const f of fs.readdirSync(dir).filter(f => f.endsWith(".json") && !f.startsWith("_") && !/borderline|gotfriends/.test(f))) {
    const b = f.split(/[_.]/)[0]; if (!BOARDS.includes(b)) continue;
    (raw[b] ||= []).push(...JSON.parse(fs.readFileSync(path.join(dir, f), "utf8")));
  }
} else {
  let lim = null;
  if (PAID.length) {
    if (!T) { console.log("APIFY_API_TOKEN missing"); process.exit(1); }
    lim = (await apify("/users/me/limits")).data;
    const left = lim.limits.maxMonthlyUsageUsd - lim.current.monthlyUsageUsd;
    console.log(`Apify budget left: $${left.toFixed(2)}`);
    if (left < estimate() * 2) { console.log("not enough Apify budget for a safe run — stopping"); process.exit(2); }
  }
  fs.mkdirSync(path.join(OUT, "raw"), { recursive: true });
  await Promise.all([
    ...PAID.flatMap(b => ACTORS[b].runs().map(async (input, i) => {
      const t0 = Date.now();
      try { const r = await runActor(ACTORS[b].id, input); (raw[b] ||= []).push(...r.items); runLog.push({ board: b, run: i, status: r.status, items: r.items.length, sec: Math.round((Date.now() - t0) / 1000) }); }
      catch (e) { runLog.push({ board: b, run: i, error: e.message }); }
    })),
    ...FREEB.map(async b => {
      const t0 = Date.now();
      try { const r = await FREE[b](); (raw[b] ||= []).push(...r.items); runLog.push({ board: b, free: true, items: r.items.length, sec: Math.round((Date.now() - t0) / 1000), detail: r.log }); }
      catch (e) { runLog.push({ board: b, free: true, error: e.message }); }
    }),
  ]);
  for (const [b, items] of Object.entries(raw)) fs.writeFileSync(path.join(OUT, "raw", `${b}.json`), JSON.stringify(items, null, 1));
  if (lim) { const after = (await apify("/users/me/limits")).data.current.monthlyUsageUsd; runLog.push({ spentUsd: +(after - lim.current.monthlyUsageUsd).toFixed(3) }); }
}
fs.mkdirSync(OUT, { recursive: true });

// normalize + filter + dedupe
const stats = {}; const byKey = new Map();
for (const b of BOARDS) {
  const items = raw[b] || []; const s = stats[b] = { pulled: items.length, title: 0, loc: 0, days: 0, kept: 0 };
  const seenUrl = new Set();
  for (const it of items) {
    const j = { board: b, ...NORM[b](it) }; j.dayOnly = /T21:59:59\.000Z$/.test(j.posted || "");
    if (!j.url || seenUrl.has(j.url)) continue; seenUrl.add(j.url);
    if (!ALL_TITLES && titleVerdict(j.title) === "out") { s.title++; continue; }
    if (!locOk(j)) { s.loc++; continue; }
    if (!daysOk(j)) { s.days++; continue; }
    s.kept++;
    const k = normKey(j);
    if (byKey.has(k)) { const o = byKey.get(k); if (!o.boards.includes(b)) o.boards.push(b); o.links[b] = j.url; if (!o.company || o.company === "(חסוי)") o.company = j.company; continue; }
    byKey.set(k, { ...j, boards: [b], links: { [b]: j.url } });
  }
}
let results = [...byKey.values()].sort((a, b) => (b.posted || "").localeCompare(a.posted || ""));

if (MATCH && results.length) {
  const { assess } = await import("../match.mjs");
  console.log(`match brain on ${results.length} jobs (~$${(results.length * 0.0055).toFixed(2)})…`);
  for (const j of results) { try { const a = await assess({ title: j.title, company: j.company, countries: ["Israel"], desc: j.desc }); j.fit = a.fit; j.verdict = a.verdict || ""; } catch (e) { j.fit = null; j.verdict = "error"; } }
  results.sort((a, b) => (b.fit ?? -1) - (a.fit ?? -1));
}

// write
const slim = results.map(({ desc, ...r }) => ({ ...r, descStart: String(desc || "").replace(/\s+/g, " ").slice(0, 300) }));
fs.writeFileSync(path.join(OUT, "results-full.json"), JSON.stringify(results)); // with full descriptions, for the app's scorer
fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ keyword: KW, he: HE, loc: LOC, days: DAYS, boards: BOARDS, runLog, stats, results: slim }, null, 1));
const csvCell = v => `"${String(v ?? "").replace(/"/g, '""')}"`;
const cols = ["posted", "title", "company", "location", "boards", "easyApply", "poster", ...(MATCH ? ["fit", "verdict"] : []), "url"];
fs.writeFileSync(path.join(OUT, "results.csv"), "﻿" + [cols.join(","), ...slim.map(r => cols.map(c => csvCell(c === "boards" ? r.boards.join("+") : r[c])).join(","))].join("\n"));

// report
console.log("\nper board (pulled → dropped by title / location / age → kept):");
for (const [b, s] of Object.entries(stats)) console.log(`  ${b.padEnd(9)} ${String(s.pulled).padStart(3)} → -${s.title} / -${s.loc} / -${s.days} → ${s.kept}`);
if (runLog.length) console.log("runs:", JSON.stringify(runLog));
console.log(`\n${results.length} jobs after dedupe:\n`);
for (const r of results) console.log(`  ${r.posted || "  ?       "} ${MATCH ? String(r.fit ?? "-").padStart(3) + " " : ""}${r.boards.join("+").padEnd(16)} ${String(r.title).slice(0, 60).padEnd(60)} · ${String(r.company || "").slice(0, 28)} · ${String(r.location || "").slice(0, 24)}${r.easyApply ? " · Easy Apply" : ""}`);
console.log(`\nsaved: ${OUT}`);
