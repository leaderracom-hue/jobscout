"""Phase 1 fixes (26 Nov): one open-jobs count, sent/undo keeps the job text and the earlier state, cut-off Claude replies fail clearly,
the outreach page explains what is missing. No network: GitHub writes and reads are replaced with an in-memory state."""
import copy
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HEAD = (ROOT / "app.py").read_text(encoding="utf-8").split("# ---------------- app shell")[0]


def job(key, fit=70, **kw):
    return {"key": key, "title": "Product Manager", "company": key, "location": "Tel Aviv", "boards": ["alljobs"], "fit": fit,
            "desc": "x" * 5000, **kw}


def fixture_state():
    return {"jobs": {"a": job("a"), "b": job("b", fit=20), "c": job("c", fit=20, forced=True), "d": job("d"), "e": job("e", company="acme")},
            "archive": {"d": {"status": "removed"}}, "runs": [],
            "settings": {"exclude_words": [], "keep_words": [], "blocked_companies": ["acme"], "followed_companies": []}}


@pytest.fixture()
def app():
    ns = {"__name__": "app_under_test", "__file__": str(ROOT / "app.py")}
    exec(compile(HEAD.replace("\ngate()\n", "\n"), "app_head", "exec"), ns)  # no password screen outside a real run
    box = {"s": fixture_state()}

    def update_state(mutate, message):
        mutate(box["s"])
        return box["s"]
    ns["update_state"] = update_state
    ns["load_state"] = lambda: (copy.deepcopy(box["s"]), "sha")
    ns["box"] = box
    return ns


def test_one_open_count(app):
    shown, hidden = app["open_jobs"](app["box"]["s"])
    assert sorted(j["key"] for j in shown) == ["a", "c"]  # b under the threshold, d archived, e blocked company
    assert [j["key"] for j in hidden] == ["e"]


def test_sent_keeps_text_and_undo_restores_earlier_state(app):
    app["mark_many"](["a", "d"], "sent")
    s = app["box"]["s"]
    assert s["archive"]["a"]["status"] == "sent" and len(s["jobs"]["a"]["desc"]) == 3000
    u = app["st"].session_state.undo
    app["restore_many"](u["keys"], u.get("blocked") or [], u.get("prev"))
    assert "a" not in s["archive"] and s["archive"]["d"] == {"status": "removed"}


def test_cut_off_reply_fails_clearly(app):
    def reply(stop, text):
        return types.SimpleNamespace(stop_reason=stop, content=[types.SimpleNamespace(type="text", text=text)] if text else [])
    with pytest.raises(RuntimeError):
        app["_json_reply"](reply("max_tokens", '{"a":'))
    with pytest.raises(RuntimeError):
        app["_json_reply"](reply("end_turn", ""))
    assert app["_json_reply"](reply("end_turn", '{"a": 1}')) == {"a": 1}


def test_outreach_page_explains_missing_contact():
    from streamlit.testing.v1 import AppTest
    script = ROOT / "_test_outreach_page_tmp.py"  # beside app.py: the app finds its engine files from its own folder
    script.write_text(HEAD + '''
import copy as _c
_S = {"jobs": {"k": {"key": "k", "title": "PM", "company": "Wix", "url": "https://x", "fit": 80}}, "archive": {}, "runs": []}
load_state = lambda: (_c.deepcopy(_S), "sha")
update_state = lambda m, msg: None
secret = lambda name, default=None: None
page_outreach()
''', encoding="utf-8")
    at = AppTest.from_file(str(script), default_timeout=60)
    at.session_state["ok"] = True
    try:
        at.run()
    finally:
        script.unlink()
    assert not at.exception
    assert any("SMARTLEAD_API_KEY" in c.value for c in at.caption)
    assert any("כדי להמשיך" in i.value for i in at.info), ([t.value for t in at.text_input], [c.value for c in at.caption], [e.value for e in at.error])
    assert at.button[0].disabled
