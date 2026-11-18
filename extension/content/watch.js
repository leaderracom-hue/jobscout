// watch.js — injected into the application tab after every page load; tells the extension once the site shows
// its "thank you / application received" page, so the job is recorded as sent.
(() => {
  if (window.__jobScoutWatch) return;
  window.__jobScoutWatch = true;
  const CONFIRM = /thank\s*you\s*for\s*(applying|your\s*(application|interest))|thanks\s*for\s*applying|application\s*(has\s*been\s*)?(received|submitted|sent)|we('ve|\s*have)\s*received\s*your|successfully\s*submitted|הגשת\s*המועמדות\s*(התקבלה|נשלחה)|קורות\s*החיים\s*נשלחו|תודה\s*על\s*(פנייתך|הגשת|מועמדותך)/i;
  const check = () => {
    const text = (document.body?.innerText || "").slice(0, 6000);
    if (CONFIRM.test(text)) { clearInterval(iv); chrome.runtime.sendMessage({ type: "confirmed" }); }
  };
  const iv = setInterval(check, 2000);
  check();
})();
