/**
 * Projects & contracts finder (28 Nov): freelance / contract / B2B / LLC work open to someone in Israel. Free public feeds,
 * no keys, no Apify. Sources were probed on 28 Nov; ones that block bots, need a subscription or only list full-time jobs are left out
 * (Startup.jobs, EvenLeads, JustJoin.it /api, WWR, Bootstrapped, RemoteAnywhere, Traction, JobQuery, Opening, EU Remote Jobs).
 * Usage: node find-projects.mjs [--sources a,b] [--days 30] [--out file.json] | node find-projects.mjs --self-check
 * Output JSON: { items: [...], log: { source: {pulled, kept, error?} }, dropped: {reason: n} }
 */
import fs from "node:fs";
import assert from "node:assert/strict";

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const DAYS = Number(arg("--days", 30));
const CUTOFF = Date.now() - DAYS * 864e5;
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36";

export const strip = (h) => String(h || "").replace(/<[^>]+>/g, " ").replace(/&#x2F;/g, "/").replace(/&#x27;|&#39;|&apos;/g, "'").replace(/&quot;/g, '"')
  .replace(/&amp;/g, "&").replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();
const iso = (t) => { const d = new Date(typeof t === "number" && t < 1e12 ? t * 1000 : t); return Number.isNaN(+d) ? "" : d.toISOString(); };
const money = (lo, hi, unit) => (Number(lo) || Number(hi) ? `${Math.round(lo) || "?"}–${Math.round(hi) || "?"} ${unit}`.trim() : "");
async function get(url, json = true) {
  for (let i = 0; i < 2; i++) {
    try {
      const r = await fetch(url, { headers: { "User-Agent": UA, Accept: json ? "application/json" : "text/html" }, signal: AbortSignal.timeout(25000) });
      if (r.ok) return json ? r.json() : r.text();
      if (r.status < 500 && r.status !== 429) throw new Error(`HTTP ${r.status}`);
    } catch (e) { if (i || /HTTP 4/.test(e.message)) throw e; }
    await new Promise((s) => setTimeout(s, 1500));
  }
  throw new Error(`failed ${url}`);
}
async function pool(items, n, fn) { const out = []; let i = 0; await Promise.all(Array.from({ length: n }, async () => { while (i < items.length) { const k = i++; out[k] = await fn(items[k]).catch(() => null); } })); return out; }

// ---------- filters (each has a self-check below) ----------
// the fields asked for: AI Agents, AI/Business/Marketing Automation, CRM, ERP, GTM Engineering, n8n, Make, MCP, Voice AI, System Integrations
export const TOPIC = /\b(ai[- ]?(agents?|automation|consultant|product|solutions?|implementation|specialist|builder|engineer|integration|workflows?|transformation|ops)|agentic|llm|chatbot|gpt|claude|automations?|automate|workflows?|n8n|zapier|make\.com|make scenarios?|integromat|mcp|voice (ai|agents?|bots?)|vapi|retell|crm|hubspot|salesforce|gohighlevel|ghl|pipedrive|erp|netsuite|odoo|gtm|go[- ]to[- ]market|rev ?ops|marketing (automation|ops|operations|technology)|martech|lead gen\w*|integrations?|api integration|airtable|product manager|project manager|implementation)\b/i;
export const DROP = /\b(qa|tester|testing engineer|translat\w*|interpret\w*|linguist|annotat\w*|rater|ai trainer|data entry|virtual assistant|commission[- ]only|commission based|unpaid|volunteer|equity[- ]only|internship|intern|cyber\w*|penetration|video edit\w*|graphic design\w*|logo|onlyfans|crypto trading bot|homework)\b|\b(german|french|dutch|bahasa|spanish|portuguese|italian)[- ]speaking|(fluent|native|proficient)( in)? (german|french|dutch|bahasa|spanish|portuguese|italian|polish)/i;
// German/French ads and roles closed by country in the title
export const NON_EN = /\((m\/w\/d|w\/m\/d|h\/f|f\/h)\)|\b(vertrieb\w*|freiberufl\w*|mitarbeiter\w*|teilzeit|homeoffice|alternance)\b|\b(philippines|india|latam|us market|france|germany|dach)\b/i;
export const relevant = (t) => TOPIC.test(t) && !DROP.test(t);
// a forum post by someone selling their own time, not hiring
export const isOffering = (t) => /\[?for hire\]?|seeking (freelance|work)|^location:|\bwilling to relocate\b|\bavailable for\b|\boffering\b|\bi (can|will) (build|help|automate)|looking for (new |small |freelance |paid )?(n8n |make |automation |ai )*(projects|work|clients|gigs|opportunit)|\bportfolio\b.*\bhire me\b|\bhire me\b/i.test(t);
// a full-time employee role posted as if it were a contract
export const fullTimeOnly = (t) => /\b(full[- ]time (employee|position|role|employment)|w-?2 only|permanent (role|position)|salary \+ benefits|401k)\b/i.test(t) && !/\b(contract|freelance|contractor|1099|b2b)\b/i.test(t);

// eligibility: 3 outcomes. block = we cannot take it from Israel; flag = must check before applying; ok.
const HARD = [
  [/\b(us|u\.s\.|usa|united states|canada|uk|u\.k\.|north america|latam|latin america|apac|india|philippines|australia)[- ]?(only|based( only)?|residents? only|citizens? only)\b/i, "מוגבל למדינה"],
  [/\bonly (open to|for|accepting) (candidates |applicants |residents |people )?(in|from|based in|located in) (the )?(us|usa|united states|canada|uk|north america|latam|india|australia)\b/i, "מוגבל למדינה"],
  [/\bmust (be )?(reside|live|located|based) (in|within) (the )?(us|usa|united states|canada|uk|north america|latam|india|australia|eu|europe|germany|poland)\b/i, "חובת מגורים"],
  [/\b(authori[sz]ed|eligible|legally able) to work in (the )?(us|usa|united states|canada|uk|eu)\b|\bus persons?\b|\bgreen card\b|\bw-?2\b|\bno (visa )?sponsorship\b.*\b(us|usa)\b/i, "דורש אישור עבודה במדינה"],
  [/\b(security|secret|government|public trust) clearance\b|\bitar\b|\bdod\b/i, "דורש סיווג ביטחוני"],
  [/\b(on[- ]?site|in[- ]office|in[- ]person) (only|required|5 days)\b|\brelocat(e|ion) (to|is required)\b|\bwithin commuting distance\b/i, "דורש הגעה פיזית"],
];
const SOFT = [
  [/\b(est|pst|cst|et|pt|cet|gmt|utc|us|eu|apac|aest)[- ]?(time ?zones?|hours|business hours)\b|\boverlap (with|of) \d+|\btime ?zone overlap\b/i, "דרישת אזור זמן: לבדוק"],
  [/\b(residents?|located|based) (in|within) (the )?(eu|europe|eea|emea)\b|\beu only\b|\beurope only\b/i, "אירופה בלבד: לבדוק אם מגורים או רק אזרחות"],
  [/\blocal (candidates|applicants|talent)\b|\bresidents only\b|\bpreferably (based|located) in\b/i, "ניסוח מעורפל על מיקום: לבדוק"],
];
export function eligibility(text) {
  const t = String(text || "");
  const open = /\b(worldwide|anywhere|global(ly)?|any (country|location|time ?zone)|israel)\b/i.test(t);
  for (const [rx, why] of HARD) { const m = t.match(rx); if (m && !(open && why === "מוגבל למדינה")) return { status: "block", reason: why, match: m[0] }; }
  const flags = SOFT.filter(([rx]) => rx.test(t)).map(([rx, why]) => ({ reason: why, match: t.match(rx)[0] }));
  return { status: flags.length ? "flag" : "ok", flags };
}
// Jerusalem area is out unless another city is listed (Assaf 26 Nov)
export const jerusalemOnly = (loc) => /jerusalem|ירושלים|beit shemesh|בית שמש/i.test(loc || "") && !/tel aviv|תל אביב|remote|herzliya|ramat gan|haifa|petah|רמת גן|הרצליה|anywhere|worldwide/i.test(loc || "");

// URL key for dedupe: no tracking params, no trailing slash, no www
export function canonUrl(u) {
  try { const x = new URL(u); for (const k of [...x.searchParams.keys()]) if (/^(utm_|ref|source|gh_src|lever-|fbclid|gclid)/i.test(k)) x.searchParams.delete(k);
    return (x.hostname.replace(/^www\./, "") + x.pathname.replace(/\/+$/, "") + (x.search || "")).toLowerCase(); } catch { return String(u || "").toLowerCase(); }
}
const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9א-ת]+/g, " ").trim();

// ---------- sources ----------
async function discourse(base, listPath, label) {
  const list = await get(`${base}${listPath}`);
  const users = Object.fromEntries((list.users || []).map((u) => [u.id, u]));
  const topics = (list.topic_list?.topics || []).filter((t) => !t.pinned && new Date(t.created_at).getTime() >= CUTOFF && !isOffering(t.title));
  const full = await pool(topics, 4, (t) => get(`${base}/t/${t.id}.json`));
  return topics.map((t, i) => {
    const post = full[i]?.post_stream?.posts?.[0]; const u = users[t.posters?.[0]?.user_id] || {};
    const who = post?.username || u.username;
    return { source: label, title: t.title, company: "", url: `${base}/t/${t.slug}/${t.id}`, posted: iso(t.created_at), desc: strip(post?.cooked).slice(0, 6000),
      budget: "", engagement: "פרויקט / פרילנס", direct: true,
      poster: who ? { name: post?.name || u.name || who, role: "פרסם את הבקשה", profile: `${base}/u/${who}` } : null };
  });
}
// LinkedIn search returns mostly opinion and self-promotion: keep a post only when its first line says someone is hiring.
export const HIRE_LINE = /#\s*hiring|\bwe(['’]| a)?re hiring\b|\bhiring:|\blooking for (an? |experienced )?([\w&.-]+ ){0,3}(experts?|developers?|freelancers?|contractors?|consultants?|builders?|engineers?)\b|\bneed (an? )?([\w&.-]+ ){0,3}(expert|developer|freelancer)\b/i;
export const NOT_HIRE = /\b(open to work|my next (remote )?role|i['’]?m available|join our community|whatsapp community|let['’]?s connect)\b/i;
export function linkedinRow(p) {
  const first = String(p.content || "").split("\n").find((l) => l.trim()) || "";
  if (!HIRE_LINE.test(first) || NOT_HIRE.test(first)) return null;
  return { source: "LinkedIn Posts", title: first.slice(0, 140), company: "", url: p.linkedinUrl, posted: p.postedAt?.date || "",
    desc: String(p.content || "").slice(0, 6000), budget: "", engagement: "פוסט", location: "", direct: true,
    poster: { name: p.author?.name, role: p.author?.info || "", profile: String(p.author?.linkedinUrl || "").split("?")[0] } };
}
export const SOURCES = {
  n8n: { label: "n8n Community", kind: "API", run: () => discourse("https://community.n8n.io", "/c/jobs/13.json", "n8n Community") },
  make: { label: "Make Hire Help", kind: "API", run: () => discourse("https://community.make.com", "/c/hire-a-pro/hire-help/l/latest.json", "Make Hire Help") },
  // 28 Nov: PAID (Apify harvestapi, ~$0.002/post, ~$0.16/run). Off by default; the page runs it only after a per-run confirm.
  linkedin: { label: "LinkedIn Posts", kind: "Apify", async run() {
    const tok = process.env.APIFY_API_TOKEN;
    if (!tok) throw new Error("missing APIFY_API_TOKEN");
    const r = await fetch("https://api.apify.com/v2/acts/harvestapi~linkedin-post-search/run-sync-get-dataset-items?timeout=240", {
      method: "POST", headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" }, signal: AbortSignal.timeout(260000),
      body: JSON.stringify({ searchQueries: ["looking for n8n freelancer", "hiring AI automation contractor", "looking for AI agent developer freelance",
        "need make.com automation expert"], maxPosts: 25, postedLimit: "week", sortBy: "date" }) });
    if (!r.ok) throw new Error(`Apify HTTP ${r.status}`);
    return (await r.json()).map(linkedinRow).filter(Boolean);
  } },
  // 28 Nov: official Reddit API, app-only OAuth (free app at reddit.com/prefs/apps). Only [Hiring] posts; the poster is the decision maker.
  reddit: { label: "Reddit", kind: "API", async run() {
    const id = process.env.REDDIT_CLIENT_ID, secret = process.env.REDDIT_CLIENT_SECRET;
    if (!id || !secret) throw new Error("missing REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET");
    const ua = "job-scout-projects/1.0";
    const t = await fetch("https://www.reddit.com/api/v1/access_token", { method: "POST", body: "grant_type=client_credentials", signal: AbortSignal.timeout(20000),
      headers: { Authorization: "Basic " + Buffer.from(`${id}:${secret}`).toString("base64"), "Content-Type": "application/x-www-form-urlencoded", "User-Agent": ua } });
    if (!t.ok) throw new Error(`Reddit auth HTTP ${t.status}`);
    const tok = (await t.json()).access_token;
    const out = [];
    for (const sub of ["forhire", "freelance_forhire", "n8n", "automation"]) {
      const r = await fetch(`https://oauth.reddit.com/r/${sub}/new?limit=100&raw_json=1`, { headers: { Authorization: `Bearer ${tok}`, "User-Agent": ua }, signal: AbortSignal.timeout(20000) });
      if (!r.ok) throw new Error(`Reddit r/${sub} HTTP ${r.status}`);
      for (const { data: d } of (await r.json()).data.children) {
        if (!/\[\s*hiring\s*\]|\bhiring\b|looking for (an? )?(freelancer|developer|expert|contractor)/i.test(d.title)) continue;
        out.push({ source: "Reddit", title: d.title, company: "", url: `https://www.reddit.com${d.permalink}`, posted: iso(d.created_utc),
          desc: String(d.selftext || "").slice(0, 6000), budget: "", engagement: "פרויקט", location: "", direct: true,
          poster: { name: d.author, role: `פרסם ב-r/${sub}`, profile: `https://www.reddit.com/user/${d.author}` } });
      }
    }
    return out;
  } },
  hn: { label: "Hacker News", kind: "API", async run() {
    const s = (await get("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10")).hits.find((h) => /who is hiring/i.test(h.title));
    if (!s) return [];
    const seen = new Map();
    for (const q of ["contract", "freelance", "contractor"]) {
      const j = await get(`https://hn.algolia.com/api/v1/search?tags=comment,story_${s.objectID}&query=${q}&hitsPerPage=200`);
      for (const c of j.hits) {
        const t = strip(c.comment_text);
        if (String(c.parent_id) !== s.objectID || !/\b(contract(or|s)?|freelance\w*)\b/i.test(t) || !/\bremote\b/i.test(t)) continue;
        const head = t.split(/\s\|\s|\n/)[0];
        seen.set(c.objectID, { source: "Hacker News", title: t.slice(0, 120), company: head.slice(0, 60), url: `https://news.ycombinator.com/item?id=${c.objectID}`, posted: iso(c.created_at),
          desc: t.slice(0, 6000), budget: "", engagement: "חוזה", direct: true, poster: { name: c.author, role: "פרסם את המודעה", profile: `https://news.ycombinator.com/user?id=${c.author}` } });
      }
    }
    return [...seen.values()];
  } },
  himalayas: { label: "Himalayas", kind: "API", async run() {
    const out = new Map();
    for (const q of ["automation", "AI agent", "n8n", "CRM", "marketing automation", "AI consultant", "integration", "product manager"]) for (let page = 1; page <= 2; page++) {
      const j = await get(`https://himalayas.app/jobs/api/search?q=${encodeURIComponent(q)}&employment_type=Contractor&country=IL&sort=recent&page=${page}`); // country=IL: only posts open to someone in Israel;
      for (const x of j.jobs || []) out.set(x.guid, { source: "Himalayas", title: x.title, company: x.companyName, url: x.applicationLink || x.guid, posted: iso(x.pubDate),
        desc: strip(x.description).slice(0, 6000), budget: money(x.minSalary, x.maxSalary, `${x.currency || ""} ${x.salaryPeriod || ""}`), engagement: "חוזה",
        location: (x.locationRestrictions || []).length ? x.locationRestrictions.join(", ") : "Worldwide", poster: null });
      if ((j.jobs || []).length < 20) break;
    }
    return [...out.values()];
  } },
  braintrust: { label: "Braintrust", kind: "API", async run() {
    const j = await get("https://app.usebraintrust.com/api/jobs/?page_size=200"); // one call: robots.txt disallows ?page=
    return (j.results || []).filter((x) => x.openings_left !== 0 && !(x.deadline && new Date(x.deadline) < new Date())).map((x) => ({
      source: "Braintrust", title: x.title, company: x.employer?.name || "", url: `https://app.usebraintrust.com/jobs/${x.id}/`, posted: iso(x.created),
      desc: strip(x.description || x.introduction || "").slice(0, 6000), budget: money(x.budget_minimum_usd, x.budget_maximum_usd, `USD ${x.payment_type || ""}`), engagement: "חוזה / פרילנס",
      location: (x.locations || []).map((l) => l.location).join(", ") || "Worldwide", poster: null }));
  } },
  ajb: { label: "Automation Job Board", kind: "דף", async run() {
    const html = await get("https://automationjobboard.com/", false);
    const urls = [...new Set([...html.matchAll(/href="(\/jobs\/[^"#?]+)"/g)].map((m) => `https://automationjobboard.com${m[1]}`))].slice(0, 80);
    const pages = await pool(urls, 4, (u) => get(u, false));
    return urls.map((u, i) => {
      const jp = [...String(pages[i] || "").matchAll(/<script[^>]*application\/ld\+json[^>]*>([\s\S]*?)<\/script>/g)].map((m) => { try { return JSON.parse(m[1]); } catch { return null; } }).find((o) => o?.["@type"] === "JobPosting");
      if (!jp || !/CONTRACTOR|TEMPORARY|PART_TIME|PER_DIEM|OTHER/.test(JSON.stringify(jp.employmentType || ""))) return null; // full-time only is not a project
      return { source: "Automation Job Board", title: jp.title, company: jp.hiringOrganization?.name || "", url: u, posted: iso(jp.datePosted), desc: strip(jp.description).slice(0, 6000),
        budget: "", engagement: String(jp.employmentType), location: JSON.stringify(jp.applicantLocationRequirements || jp.jobLocation || "") , poster: null };
    }).filter(Boolean);
  } },
};
export const LINKEDIN_LINK = "https://www.linkedin.com/search/results/content/?keywords=%22looking%20for%22%20(n8n%20OR%20%22AI%20agent%22%20OR%20%22automation%20expert%22)%20(freelancer%20OR%20contractor)&datePosted=%22past-week%22";

// ---------- pipeline ----------
export function filterAll(rows) {
  const items = [], dropped = {}, seen = new Set(), kept = {};
  const drop = (r) => { dropped[r] = (dropped[r] || 0) + 1; };
  for (const g of rows) {
    const text = `${g.title} ${g.desc || ""}`, loc = `${g.location || ""} ${g.desc || ""}`;
    if (!g.title || !g.url) { drop("חסר"); continue; }
    if (g.posted && new Date(g.posted).getTime() < CUTOFF) { drop("ישן"); continue; }
    // forum posts are judged on the whole post; job boards on the title (a sales job that mentions "CRM" in its text is not a project)
    if (!relevant(g.direct ? text : g.title) || DROP.test(g.title) || NON_EN.test(g.title)) { drop("לא בתחום"); continue; }
    if (g.direct && isOffering(g.title + " " + String(g.desc || "").slice(0, 300))) { drop("מציע את עצמו"); continue; }
    if (fullTimeOnly(text)) { drop("משרה מלאה"); continue; }
    if (jerusalemOnly(g.location)) { drop("אזור ירושלים"); continue; }
    const el = eligibility(loc);
    if (el.status === "block") { drop(el.reason); continue; }
    const k1 = canonUrl(g.url), k2 = g.company ? `${norm(g.title)}|${norm(g.company)}` : g.source === "LinkedIn Posts" ? `li|${norm(g.title)}` : null;  // reshared LinkedIn posts
    if (seen.has(k1) || (k2 && seen.has(k2))) { drop("כפילות"); continue; }
    seen.add(k1); if (k2) seen.add(k2);
    kept[g.source] = (kept[g.source] || 0) + 1;
    items.push({ ...g, id: k1, eligibility: el.status, flags: el.flags || [] });
  }
  items.sort((a, b) => String(b.posted).localeCompare(String(a.posted)));
  return { items, dropped, kept };
}

function selfCheck() {
  assert.ok(linkedinRow({ content: "🚀 Looking for n8n Automation Experts!\nDM me" }));
  assert.ok(linkedinRow({ content: "We're Hiring | Freelance Automation & AI Developer" }));
  assert.ok(linkedinRow({ content: "We’re Hiring | Freelance Automation & AI Developer" }));  // curly apostrophe, as LinkedIn writes it
  assert.ok(!linkedinRow({ content: "AI isn't firing people. It's just not hiring them." }));
  assert.ok(!linkedinRow({ content: "I'm looking for my next remote role as an Operations Coordinator" }));
  assert.ok(relevant("Looking for an n8n expert to build a lead workflow"));
  assert.ok(relevant("Voice AI agent with Vapi for a dental clinic"));
  assert.ok(relevant("HubSpot CRM integration contractor"));
  assert.ok(!relevant("Senior QA tester for mobile app"));
  assert.ok(!relevant("Translate our docs to Spanish"));
  assert.ok(!relevant("Graphic design for logo"));
  assert.ok(!relevant("AI automation, fluent German required"));
  assert.ok(NON_EN.test("Sales Setter - Vertrieb (w/m/d)") && NON_EN.test("Executive Assistant | Philippines") && !NON_EN.test("n8n Automation Consultant"));
  assert.ok(isOffering("[For Hire] n8n expert available"));
  assert.ok(isOffering("Looking for small n8n automation projects"));
  assert.ok(!isOffering("Looking for an n8n developer for ongoing project"));
  assert.ok(fullTimeOnly("Full-time employee position, salary + benefits"));
  assert.ok(!fullTimeOnly("Full-time contract, 6 months, B2B"));
  assert.equal(eligibility("Remote, US only").status, "block");
  assert.equal(eligibility("Must be authorized to work in the United States").status, "block");
  assert.equal(eligibility("Requires active security clearance").status, "block");
  assert.equal(eligibility("Must reside in the UK").status, "block");
  assert.equal(eligibility("On-site required in Austin").status, "block");
  assert.equal(eligibility("Remote, worldwide. US-based team.").status, "ok");
  assert.equal(eligibility("Fully remote, anywhere").status, "ok");
  assert.equal(eligibility("Must overlap 4 hours with EST hours").status, "flag");
  assert.equal(eligibility("Candidates located in the EU").status, "flag");
  assert.equal(eligibility("Local candidates preferred").status, "flag");
  assert.equal(eligibility("").status, "ok");
  assert.ok(jerusalemOnly("Jerusalem, Israel") && !jerusalemOnly("Tel Aviv | Jerusalem") && !jerusalemOnly(""));
  assert.equal(canonUrl("https://www.x.com/jobs/1/?utm_source=a&id=2"), canonUrl("https://x.com/jobs/1?id=2"));
  const r = filterAll([
    { source: "a", title: "n8n workflow contractor", company: "Acme", url: "https://a.com/1?utm_source=x", posted: new Date().toISOString(), desc: "remote anywhere" },
    { source: "b", title: "n8n workflow contractor", company: "ACME", url: "https://b.com/9", posted: new Date().toISOString(), desc: "" },
    { source: "a", title: "Automation Expert Needed", company: "", url: "https://a.com/2", posted: new Date().toISOString(), desc: "" },
    { source: "c", title: "Automation Expert Needed", company: "", url: "https://c.com/3", posted: new Date().toISOString(), desc: "" },
    { source: "a", title: "CRM automation", company: "Z", url: "https://a.com/4", posted: new Date().toISOString(), desc: "US only" },
    { source: "a", title: "CRM automation old", company: "Z", url: "https://a.com/5", posted: "2020-01-01", desc: "" },
  ]);
  assert.equal(r.items.length, 3); // same title+company deduped; same generic title w/o company kept (different posts)
  assert.equal(r.dropped["כפילות"], 1); assert.equal(r.dropped["מוגבל למדינה"], 1); assert.equal(r.dropped["ישן"], 1);
  console.log("self-check ok");
}

async function main() {
  selfCheck();
  if (process.argv.includes("--self-check")) return;
  const pick = (arg("--sources", Object.keys(SOURCES).filter((k) => SOURCES[k].kind !== "Apify").join(","))).split(",").filter((k) => SOURCES[k]);
  const rows = [], log = {};
  for (const k of pick) {
    try { const raw = await SOURCES[k].run(); rows.push(...raw); log[k] = { pulled: raw.length }; }
    catch (e) { log[k] = { pulled: 0, error: String(e.message).slice(0, 120) }; }
  }
  const { items, dropped, kept } = filterAll(rows);
  for (const k of pick) log[k].kept = kept[SOURCES[k].label] || 0;
  const res = { at: new Date().toISOString(), days: DAYS, items, log, dropped };
  const out = arg("--out", null);
  if (out) fs.writeFileSync(out, JSON.stringify(res)); else console.log(JSON.stringify({ log, dropped, n: items.length }, null, 1));
}
main().catch((e) => { console.error(e); process.exit(1); });
