import datetime as dt

from core import projects as PJ

NOW = dt.datetime(2026, 11, 28, tzinfo=dt.timezone.utc)


def item(i, days_ago, **kw):
    return {"id": i, "title": f"t{i}", "posted": (NOW - dt.timedelta(days=days_ago)).isoformat(), "source": "n8n", **kw}


def test_merge_keeps_score_and_drops_old():
    stored = {"a": {**item("a", 2), "fit": 80, "why": "x"}, "old": item("old", 40)}
    out = PJ.merge(stored, [item("a", 2, title="new title"), item("b", 1)], now=NOW)
    assert set(out) == {"a", "b"}
    assert out["a"]["fit"] == 80 and out["a"]["title"] == "new title"


def test_merge_bad_date_uses_first_seen():
    assert "x" in PJ.merge({}, [{"id": "x", "posted": "garbage"}], now=NOW)
    stale = {"x": {"id": "x", "posted": "", "first_seen": (NOW - dt.timedelta(days=40)).isoformat()}}
    assert "x" not in PJ.merge(stale, [], now=NOW)


def test_rows_block_excel_formulas():
    assert PJ.rows([{"title": "=HYPERLINK(1)"}])[0]["title"] == "'=HYPERLINK(1)"


def test_rows_mark_missing_as_not_found():
    r = PJ.rows([{"title": "t", "poster": None}])[0]
    assert r["company"] == "לא נמצא" and r["poster"] == "לא נמצא" and r["poster_profile"] == "לא נמצא"
    assert list(r) == PJ.COLUMNS


def test_sources_match_engine():
    import pathlib
    import re
    src = (pathlib.Path(__file__).parent.parent / "engine/projects/find-projects.mjs").read_text(encoding="utf-8")
    body = re.search(r"export const SOURCES = \{(.*?)\n\};", src, re.S)
    assert body, "SOURCES block not found"
    for k in PJ.SOURCES:
        assert re.search(rf"\b{k}\s*[:,(]", body.group(1)), k


def test_cost_and_tabs():
    assert PJ.cost("li_intent", 100) == 0.2 and PJ.cost("reddit_intent", 50) == 0.12
    assert set(PJ.PAID) <= set(PJ.SOURCES) and all(PJ.SOURCES[k][2] in ("proj", "buy") for k in PJ.SOURCES)
    assert PJ.tab_of({"engagement": "Buy intent"}) == "buy" and PJ.tab_of({}) == "proj"
