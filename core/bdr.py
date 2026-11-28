"""LinkedIn BDR (28 Nov): read the BDR campaigns in HeyReach (MCP) and Smartlead (REST), pause/resume HeyReach.
Never starts a campaign: starting stays with Assaf in HeyReach / Smartlead. No Streamlit here."""
import json
import requests

HR_URL = "https://mcp.heyreach.io/mcp"
SL_API = "https://server.smartlead.ai/api/v1"
PREFIX = "BDR"   # campaigns whose name starts with this are shown
STATUS_HE = {"IN_PROGRESS": "רץ", "SCHEDULED": "מתוזמן", "PAUSED": "מושהה", "DRAFT": "טיוטה", "DRAFTED": "טיוטה", "ACTIVE": "רץ",
             "FINISHED": "הסתיים", "COMPLETED": "הסתיים", "STOPPED": "נעצר", "FAILED": "נכשל", "CANCELED": "בוטל"}
ALLOWED = {"pause_campaign", "resume_campaign", "get_all_campaigns", "get_overall_stats", "get_leads_from_list", "get_conversations_v2"}


def _sse(text):
    lines = [l for l in text.splitlines() if l.startswith("data: ")]
    return json.loads(lines[-1][6:] if lines else text)


def hr(key, name, args):
    """One HeyReach MCP tool call. Only read tools plus pause/resume are allowed, so this can never start a campaign."""
    assert name in ALLOWED, name
    r = requests.post(HR_URL, params={"xMcpKey": key}, timeout=60, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    r.raise_for_status()
    j = _sse(r.text)
    if j.get("error"):
        raise RuntimeError(f"{name}: {str(j['error'])[:300]}")
    text = "".join(c.get("text", "") for c in j["result"].get("content", []))
    if j["result"].get("isError"):
        raise RuntimeError(f"{name}: {text[:300]}")
    try:
        return json.loads(text)
    except ValueError:
        return text


def sl(key, path):
    r = requests.get(SL_API + path, params={"api_key": key}, timeout=60, headers={"User-Agent": "curl/8.4.0"})
    r.raise_for_status()
    return r.json()


def hr_campaigns(key):
    items = hr(key, "get_all_campaigns", {"offset": 0, "limit": 100}).get("items", [])   # shortcut: first 100 campaigns only, page if the account grows
    return [c for c in items if (c.get("name") or "").startswith(PREFIX)]


def hr_stats(key, ids):
    s = hr(key, "get_overall_stats", {"campaignIds": ids, "startDate": "2026-01-01T00:00:00Z", "endDate": "2030-01-01T00:00:00Z"})
    return (s or {}).get("overallStats") or {}


def sl_campaigns(key):
    out = []
    for c in sl(key, "/campaigns"):
        if (c.get("name") or "").startswith(PREFIX):
            a = sl(key, f"/campaigns/{c['id']}/analytics")
            out.append({"id": c["id"], "name": c["name"], "status": a.get("status") or c.get("status"),
                        "leads": (a.get("campaign_lead_stats") or {}).get("total", 0), **{k: int(a.get(k) or 0) for k in ("sent_count", "open_count", "reply_count", "bounce_count")}})
    return out


def card(c):
    """A HeyReach campaign as the numbers the page shows."""
    p = c.get("progressStats") or {}
    return {"id": c["id"], "name": c["name"], "status": c.get("status"), "status_he": STATUS_HE.get(c.get("status"), c.get("status")),
            "leads": p.get("totalUsers", 0), "in_progress": p.get("totalUsersInProgress", 0), "finished": p.get("totalUsersFinished", 0),
            "failed": p.get("totalUsersFailed", 0), "list_id": c.get("linkedInUserListId"),
            "can_pause": c.get("status") in ("IN_PROGRESS", "SCHEDULED"), "can_resume": c.get("status") == "PAUSED"}


def lead_rows(items):
    """HeyReach list leads -> table rows with the copy each one gets."""
    out = []
    for x in items:
        cf = {f.get("name"): f.get("value") for f in x.get("customFields") or []}
        out.append({"name": f"{x.get('firstName') or ''} {x.get('lastName') or ''}".strip(), "company": x.get("companyName") or "",
                    "title": x.get("position") or "", "profile": x.get("profileUrl") or "",
                    "connection_note": cf.get("connection_note", ""), "message_1": cf.get("message_1", ""), "message_2": cf.get("message_2", "")})
    return out


def list_leads(key, list_id):
    out = []
    for off in range(0, 5000, 100):
        items = hr(key, "get_leads_from_list", {"listId": list_id, "offset": off, "limit": 100}).get("items", [])
        out += items
        if len(items) < 100:
            break
    return lead_rows(out)


def reply_rows(items):
    """HeyReach conversations -> who wrote back. shortcut: field names follow the public API docs (no reply seen yet on 28 Nov), check on the first real reply."""
    out = []
    for c in items:
        p = c.get("correspondentProfile") or {}
        out.append({"name": f"{p.get('firstName') or ''} {p.get('lastName') or ''}".strip(), "company": p.get("companyName") or "",
                    "last_message": c.get("lastMessageText") or "", "at": (c.get("lastMessageAt") or "")[:16].replace("T", " "),
                    "profile": p.get("profileUrl") or "", "seen": c.get("read", c.get("seen"))})
    return out


def replies(key, ids):
    return reply_rows(hr(key, "get_conversations_v2", {"campaignIds": ids, "offset": 0, "limit": 100}).get("items", []))
