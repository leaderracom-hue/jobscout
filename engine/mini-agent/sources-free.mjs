// sources-free.mjs — the $0 sources for scout.mjs: Drushim search pages (__NEXT_DATA__) and company careers boards
// (Comeet / Greenhouse / Lever / Ashby, list in ats-companies.json from discover-ats.mjs). Plain HTTP, no Apify.
// Every fetcher returns normalized jobs: { title, company, location, posted (YYYY-MM-DD), url, desc }.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36";
const iso = v => { if (!v) return ""; if (/^\d{4}-\d{2}-\d{2}$/.test(String(v))) return v + "T21:59:59.000Z"; const d = new Date(typeof v === "number" ? v : String(v)); return isNaN(d) ? "" : d.toISOString(); };
const strip = h => String(h || "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
async function get(url, ms = 15000) {
  const c = new AbortController(); const t = setTimeout(() => c.abort(), ms);
  try { const r = await fetch(url, { headers: { "User-Agent": UA, "Accept-Language": "he-IL,he;q=0.9,en;q=0.8" }, signal: c.signal }); return r.ok ? await r.text() : null; }
  catch { return null; } finally { clearTimeout(t); }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---------- Drushim ----------
export async function drushim(queries, { pages = 2 } = {}) {
  const out = [], log = [];
  for (const q of queries.filter(Boolean)) {
    for (let n = 1; n <= pages; n++) {
      const html = await get(`https://www.drushim.co.il/jobs/search/${encodeURIComponent(q)}/${n > 1 ? `?page=${n}&` : "?"}sort=date`);
      const m = html && html.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
      if (!m) { log.push({ q, page: n, status: html ? "no-data" : "http-error (possible block, stopped)" }); break; }
      let jobs = [], totalPages = 1;
      for (const qr of JSON.parse(m[1])?.props?.pageProps?.dehydratedState?.queries || []) { const p = qr?.state?.data?.pages?.[0]; if (Array.isArray(p?.jobs)) { jobs = p.jobs; totalPages = p.totalPages || 1; } }
      for (const j of jobs) out.push({ title: j.title, company: /חסוי/.test(j.companyName || "") ? "(חסוי)" : j.companyName, location: j.city || (j.cities || []).join(" | "),
        posted: iso(j.publishedAtIso), url: "https://www.drushim.co.il" + j.jobUrl, desc: strip(j.description), hot: !!j.isHot });
      log.push({ q, page: n, jobs: jobs.length });
      if (n >= totalPages) break;
      await sleep(1200); // be polite: one page every ~1 s
    }
  }
  return { items: out, log };
}

// ---------- careers boards ----------
const IL = /israel|ישראל|tel.?aviv|תל.?אביב|herzl|הרצלי|ramat.?gan|רמת.?גן|petah|petach|פתח.?תקו|haifa|חיפה|jerusalem|ירושלים|ra.?anana|רעננה|netanya|נתניה|rehovot|רחובות|holon|חולון|be.?er.?sheva|באר.?שבע|yokneam|יקנעם|kfar.?saba|כפר.?סבא|hod.?hasharon|bnei.?brak|givatayim|rishon|caesarea|airport.?city|modi.?in|ness.?ziona|or.?yehuda|\bIL\b|\bTLV\b/i;

async function comeet(c) {
  const html = await get(`https://www.comeet.com/jobs/${c.slug}/${c.uid}`); if (!html) return [];
  const m = html.match(/COMPANY_POSITIONS_DATA\s*=\s*(\[[\s\S]*?\]);/); if (!m) return [];
  return JSON.parse(m[1]).map(p => ({ title: p.name, company: p.company_name || c.name, location: [p.location?.city, p.location?.country].filter(Boolean).join(", "),
    posted: iso(p.time_updated), url: p.url_comeet_hosted_page || p.url_active_page, desc: "", workplace: p.workplace_type || "", il: (p.location?.country || "") === "IL" }));
}
async function greenhouse(c) {
  const t = await get(`https://boards-api.greenhouse.io/v1/boards/${c.slug}/jobs?content=true`); if (!t) return [];
  return (JSON.parse(t).jobs || []).map(j => ({ title: j.title, company: j.company_name || c.name, location: j.location?.name || "", posted: iso(j.first_published || j.updated_at), url: j.absolute_url, desc: strip(j.content) }));
}
async function lever(c) {
  const t = await get(`https://api.lever.co/v0/postings/${c.slug}?mode=json`); if (!t) return [];
  const arr = JSON.parse(t); if (!Array.isArray(arr)) return [];
  return arr.map(j => ({ title: j.text, company: c.name, location: [j.categories?.location, ...(j.categories?.allLocations || [])].filter(Boolean).join(" | "), posted: iso(j.createdAt), url: j.hostedUrl, desc: j.descriptionPlain || "" }));
}
async function ashby(c) {
  const t = await get(`https://api.ashbyhq.com/posting-api/job-board/${encodeURIComponent(c.slug)}`); if (!t) return [];
  return (JSON.parse(t).jobs || []).map(j => ({ title: j.title, company: c.name, location: [j.location, ...(j.secondaryLocations || []).map(s => s.location), j.address?.postalAddress?.addressCountry].filter(Boolean).join(" | "),
    posted: iso(j.publishedAt), url: j.jobUrl, desc: j.descriptionPlain || "" }));
}
const FETCH = { comeet, greenhouse, lever, ashby };

export async function careers({ concurrency = 6 } = {}) {
  const extra = process.env.JOBSCOUT_ATS_EXTRA && fs.existsSync(process.env.JOBSCOUT_ATS_EXTRA) ? JSON.parse(fs.readFileSync(process.env.JOBSCOUT_ATS_EXTRA, "utf8")) : [];
  const seen = new Set(); const list = [...JSON.parse(fs.readFileSync(path.join(HERE, "ats-companies.json"), "utf8")), ...extra].filter(c => FETCH[c.ats] && !seen.has(c.ats + c.slug) && seen.add(c.ats + c.slug));
  const out = []; const log = { boards: list.length, ok: 0, failed: 0, jobsAll: 0 };
  const queue = [...list];
  await Promise.all(Array.from({ length: concurrency }, async () => {
    for (;;) {
      const c = queue.shift(); if (!c) return;
      try { const jobs = await FETCH[c.ats](c); log.ok++; log.jobsAll += jobs.length;
        for (const j of jobs) if (j.il || IL.test(j.location)) out.push({ ...j, ats: c.ats }); }
      catch { log.failed++; }
    }
  }));
  log.jobsIsrael = out.length;
  return { items: out, log };
}

// ---------- grow the careers list: find the careers system of companies seen in a search ----------
const ATS_RX = [
  ["comeet", /comeet\.(?:com|co)\/jobs\/([a-z0-9_-]+)\/([0-9A-F]{2}\.[0-9A-F]{3})/i],
  ["greenhouse", /(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io\/(?:embed\/job_board\?for=)?([a-z0-9_-]+)/i],
  ["lever", /jobs\.(?:eu\.)?lever\.co\/([a-z0-9_.-]+)/i],
  ["ashby", /jobs\.ashbyhq\.com\/([a-z0-9_.%-]+)/i],
];
const BAD_SLUG = new Set(["embed", "jobs", "api", "v1", "j", "careers", "share"]);
export function sniffAts(text) {
  for (const [ats, rx] of ATS_RX) { const m = String(text || "").match(rx); if (m && !BAD_SLUG.has(m[1].toLowerCase())) return { ats, slug: decodeURIComponent(m[1]).toLowerCase(), uid: m[2] }; }
  return null;
}
// companies = [{ name, website?, applyUrl? }] not on the list yet. Free: the apply link first, then the website and its /careers page.
export async function discoverAts(companies, { max = 15 } = {}) {
  const found = [], queue = companies.slice(0, max);
  await Promise.all(Array.from({ length: 5 }, async () => {
    for (;;) {
      const c = queue.shift(); if (!c) return;
      let hit = sniffAts(c.applyUrl);
      if (!hit && /^https?:/.test(c.website || "")) {
        const base = c.website.replace(/\/+$/, "");
        for (const p of ["", "/careers", "/jobs"]) { const t = await get(base + p, 8000); hit = t && sniffAts(t); if (hit) break; }
      }
      if (hit) found.push({ name: c.name, ...hit });
    }
  }));
  return found;
}

