import pytest
from core import bdr


def test_hr_refuses_start():
    with pytest.raises(AssertionError):
        bdr.hr("k", "start_campaign", {"campaignId": 1})


def test_sse_parse():
    assert bdr._sse('event: message\ndata: {"a": 1}\n\n') == {"a": 1}
    assert bdr._sse('{"b": 2}') == {"b": 2}


def test_card_buttons():
    c = bdr.card({"id": 1, "name": "BDR x", "status": "IN_PROGRESS", "progressStats": {"totalUsers": 504, "totalUsersInProgress": 35}})
    assert c["leads"] == 504 and c["in_progress"] == 35 and c["can_pause"] and not c["can_resume"] and c["status_he"] == "רץ"
    assert bdr.card({"id": 2, "name": "BDR y", "status": "PAUSED"})["can_resume"]
    assert not bdr.card({"id": 3, "name": "BDR z", "status": "DRAFT"})["can_pause"]


def test_lead_rows():
    r = bdr.lead_rows([{"firstName": "Patrick", "lastName": "Jamal", "companyName": "Maxima", "profileUrl": "u",
                        "customFields": [{"name": "message_1", "value": "Thanks for connecting, Patrick."}]}])[0]
    assert r["name"] == "Patrick Jamal" and r["message_1"].startswith("Thanks") and r["connection_note"] == ""


def test_reply_rows():
    r = bdr.reply_rows([{"correspondentProfile": {"firstName": "A", "lastName": "B"}, "lastMessageText": "yes", "lastMessageAt": "2026-11-29T10:11:12Z"}])[0]
    assert r["name"] == "A B" and r["at"] == "2026-11-29 10:11"
