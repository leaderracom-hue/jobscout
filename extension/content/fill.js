// fill.js — runs inside the application page (every frame). Fills what it knows from the answer bank, uploads the CV,
// paints everything it left for Assaf in orange, and never clicks Submit.
// window.JobScoutFill({ profile, cv: { name, b64 } | null, lang, job }) → { done: [], needs: [], hasForm }
(() => {
  if (window.JobScoutFill) return;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const norm = s => String(s || "").toLowerCase().replace(/[^a-z֐-׿0-9]/g, "");

  function rules(P, he) {
    return [
      [/first\s*name|given\s*name|שם\s*פרטי/i, () => he ? P.first_name_he : P.first_name],
      [/last\s*name|surname|family\s*name|שם\s*משפחה/i, () => he ? P.last_name_he : P.last_name],
      [/^\s*(full\s*)?name\s*\*?\s*$|your\s*name|שם\s*מלא|^\s*שם\s*\*?\s*$/i, () => P.full_name],
      [/e-?mail|דוא"?ל|אימייל|מייל/i, () => P.email],
      [/phone|mobile|\btel\b|טלפון|נייד/i, () => he ? P.phone_local : P.phone],
      [/linked\s*in/i, () => P.linkedin],
      [/website|portfolio|personal\s*site|github|אתר/i, () => P.portfolio],
      [/\bcity\b|location|address|where.*(based|live)|עיר|מגורים|מיקום|כתובת/i, () => he ? P.city_he : `${P.city}, ${P.country}`],
      [/country|מדינה/i, () => P.country],
      [/sponsor/i, () => P.needs_sponsorship],
      [/authori[sz]ed|eligible\s*to\s*work|work\s*permit|right\s*to\s*work|היתר\s*עבודה/i, () => P.authorized_israel],
      [/how\s*did\s*you\s*(hear|find)|source|referr|איך\s*שמעת|איך\s*הגעת/i, () => P.how_heard],
      [/notice|start\s*date|when\s*can\s*you\s*start|availability|זמינות|מתי\s*תוכל/i, () => P.notice_period],
      [/language|שפות/i, () => P.languages],
    ];
  }
  const CONSENT = /consent|agree|privacy|terms|gdpr|accept|מסכים|מאשר|הסכמה|תנאי/i;
  const COVER = /cover\s*letter|motivation|why\s.*(join|interested|us|company|role)|anything\s*else|additional\s*info|מכתב\s*מקדים|ספר.*על\s*עצמך/i;

  const visible = el => { const r = el.getBoundingClientRect(), st = getComputedStyle(el); return el.type === "file" || (r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none"); };
  function labelOf(el) {
    const forL = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    const by = el.getAttribute("aria-labelledby");
    const byText = by ? by.split(/\s+/).map(x => document.getElementById(x)?.innerText || "").join(" ") : "";
    let near = "";
    for (let p = el.parentElement, i = 0; p && i < 4 && !near; p = p.parentElement, i++)
      near = (p.querySelector("label,legend,[class*=label],[class*=question]")?.innerText || "").trim();
    return (el.getAttribute("aria-label") || forL?.innerText || byText || el.closest("label")?.innerText || near || el.placeholder || el.name || el.id || "")
      .replace(/\s+/g, " ").trim().slice(0, 200);
  }
  // React/Vue forms ignore el.value = …; use the native setter and fire the events they listen to
  function setValue(el, v) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  }
  function orange(el) { el.style.outline = "3px solid #d97706"; el.style.outlineOffset = "2px"; }

  function fields() {
    return [...document.querySelectorAll("input, textarea, select")].filter(el => {
      const t = (el.type || el.tagName).toLowerCase();
      return !["hidden", "submit", "button", "image", "reset", "search"].includes(t) && !el.disabled && visible(el);
    });
  }

  async function fillOnce(p) {
    const P = p.profile, he = p.lang === "he", R = rules(P, he);
    const NEVER = new RegExp((P.never_fill || []).map(s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") || "$^", "i");
    const done = [], needs = [];
    const all = fields(), files = all.filter(el => el.type === "file");
    for (const el of all) {
      const label = labelOf(el), t = (el.type || el.tagName).toLowerCase();
      const required = el.required || el.getAttribute("aria-required") === "true" || /\*\s*$/.test(label);
      try {
        if (t === "file") {
          const isCv = /resume|cv|קורות/i.test(label) || files.length === 1 || el === files[0];
          if (isCv && p.cv) {
            const bytes = Uint8Array.from(atob(p.cv.b64), c => c.charCodeAt(0));
            const dt = new DataTransfer(); dt.items.add(new File([bytes], p.cv.name, { type: "application/pdf" }));
            el.files = dt.files; el.dispatchEvent(new Event("change", { bubbles: true })); el.dispatchEvent(new Event("input", { bubbles: true }));
            done.push(`קו"ח: ${p.cv.name}`);
          } else if (required) { needs.push(`קובץ: ${label}`); orange(el); }
          continue;
        }
        if (t === "checkbox" || t === "radio") { if (CONSENT.test(label) && required && !el.checked) { needs.push(`סמן הסכמה: ${label}`); orange(el); } continue; }
        if (el.value) continue;
        if (NEVER.test(label)) { if (required) { needs.push(`לא ממלא: ${label}`); orange(el); } continue; }
        if (COVER.test(label) && el.tagName === "TEXTAREA") { if (required) { needs.push(`מכתב מקדים: ${label}`); orange(el); } continue; } // phase 2: Claude writes it
        const rule = R.find(([rx]) => rx.test(label));
        const value = rule ? rule[1]() : null;
        if (value == null || value === "") { if (rule || required) { needs.push(label || el.name); orange(el); } continue; }
        if (el.tagName === "SELECT") {
          const opt = [...el.options].find(o => { const a = norm(o.text), b = norm(value); return a && (a === b || a.includes(b) || (b.includes(a) && a.length > 1)); });
          if (opt) { el.value = opt.value; el.dispatchEvent(new Event("change", { bubbles: true })); done.push(`${label}: ${opt.text.trim()}`); }
          else { needs.push(label); orange(el); }
        } else { setValue(el, String(value)); done.push(`${label}: ${String(value).slice(0, 40)}`); }
      } catch { needs.push(`${label} (שגיאה)`); orange(el); }
    }
    return { done, needs, hasForm: all.some(el => el.type === "email" || el.type === "file") };
  }

  window.JobScoutFill = async p => {
    let r = await fillOnce(p);
    if (!r.hasForm && window === window.top) { // many careers pages show the form only after "Apply"
      const btn = [...document.querySelectorAll("button, a")].find(b => /^\s*(apply( now| for this job)?|הגש(\s*מועמדות)?|הגשת מועמדות|שלח(ו)?\s*(קו"ח|קורות\s*חיים))\s*$/i.test(b.innerText || ""));
      if (btn) { btn.click(); await sleep(3000); r = await fillOnce(p); }
    }
    if (window === window.top) {
      const bar = document.createElement("div");
      bar.textContent = r.needs.length ? `Job Scout מילא ${r.done.length} שדות. ${r.needs.length} מסומנים בכתום מחכים לך. בדוק ולחץ שלח.` : `Job Scout מילא ${r.done.length} שדות. בדוק ולחץ שלח.`;
      bar.style.cssText = "position:fixed;z-index:2147483647;bottom:16px;right:16px;max-width:360px;background:#151a24;color:#fff;font:14px/1.5 system-ui;padding:10px 14px;border-radius:10px;direction:rtl;box-shadow:0 4px 16px rgba(0,0,0,.25)";
      document.body.appendChild(bar); setTimeout(() => bar.remove(), 12000);
    }
    return r;
  };
})();
