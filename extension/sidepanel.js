// sidepanel.js — the send queue from the data repo, one "Open & fill" button per job. Submit stays with Assaf.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
const STATUS = { queued: "ממתין", ready: "מולא · ממתין שתלחץ שלח", needs_you: "צריך אותך", unsupported: "אתר שעוד לא נתמך", skipped: "נסגר בלי שליחה", failed: "נכשל" };
const DOOR = { greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby", comeet: "Comeet", workable: "Workable", smartrecruiters: "SmartRecruiters", "no-account-ats": "מערכת בלי הרשמה" };
const send = msg => chrome.runtime.sendMessage(msg);

async function loadSettings() {
  const s = await chrome.storage.local.get(["ghToken", "ghRepo"]);
  $("tok").value = s.ghToken || ""; $("repo").value = s.ghRepo || "leaderracom-hue/jobscout-data";
  if (!s.ghToken) $("settings").open = true;
}
$("save").onclick = async () => {
  await chrome.storage.local.set({ ghToken: $("tok").value.trim(), ghRepo: $("repo").value.trim() || "leaderracom-hue/jobscout-data" });
  $("saved").textContent = "נשמר"; setTimeout(() => $("saved").textContent = "", 2000); render();
};
$("refresh").onclick = () => render();

function card(j) {
  const fit = j.fit ?? null, cls = fit == null ? "" : fit >= 80 ? "f-hi" : fit >= 60 ? "f-mid" : "f-lo";
  const st = j.q?.status || "queued";
  return `<div class="job" data-key="${esc(j.key)}">
    <div class="row"><span class="fit ${cls}">${fit ?? "—"}</span>
      <div><div class="t"><a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a></div>
      <div class="small muted">${esc(j.company || "—")} · ${esc(DOOR[j.door] || "אין דף הגשה בלי הרשמה")}</div></div></div>
    <div><span class="status s-${st}">${STATUS[st] || st}${j.q?.note ? " · " + esc(j.q.note) : ""}</span></div>
    <div class="small muted">קו"ח: ${esc(j.cv || "—")}</div>
    <div class="acts">
      <button class="primary" data-act="open" ${j.door ? "" : "disabled"}>פתח ומלא</button>
      <button data-act="sentByHand">שלחתי ידנית</button>
      <button data-act="remove">הוצא מהתור</button>
    </div><div class="err"></div></div>`;
}

async function render() {
  $("err").textContent = "";
  const r = await send({ type: "queue" });
  if (r?.error) { $("err").textContent = r.error; $("list").innerHTML = ""; return; }
  $("counts").textContent = `בתור ${r.jobs.length} · נשלחו היום ${r.sentToday}/${r.cap}`;
  const jobs = r.jobs.sort((a, b) => (b.fit ?? -1) - (a.fit ?? -1));
  $("list").innerHTML = jobs.length ? jobs.map(card).join("") : `<div class="empty">התור ריק. ב-Job Scout לחץ "לשליחה ←" על משרות, והן יופיעו כאן.</div>`;
}

$("list").addEventListener("click", async e => {
  const b = e.target.closest("button[data-act]"); if (!b) return;
  const box = b.closest(".job"), key = box.dataset.key, errEl = box.querySelector(".err");
  b.disabled = true; errEl.textContent = "";
  const r = await send({ type: b.dataset.act, key });
  if (r?.error) { errEl.textContent = r.error; b.disabled = false; return; }
  if (b.dataset.act === "open") { b.textContent = "נפתח, ממלא…"; return; }
  render();
});

chrome.runtime.onMessage.addListener(m => { if (m.type === "changed") render(); });
loadSettings().then(render);
