"""SmartProspect unlock must never pay for more than one contact (26 Nov audit, finding 1). No network: requests.post is replaced."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import outreach as OUT  # noqa: E402


class Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def fake_api(total, people, calls):
    def post(url, params=None, json=None, timeout=None):
        calls.append((url.rsplit("/", 1)[-1], json))
        if url.endswith("search-contacts"):
            return Resp({"data": {"list": [{"fullName": n, "title": "Recruiter", "company": {"website": "wix.com"}} for n in people],
                                  "filter_id": 7, "total_count": total}})
        return Resp({"data": {"list": [{"fullName": people[0], "email": "may@wix.com"}]}})
    return post


def test_unlocks_one_contact_when_the_title_search_leaves_one(monkeypatch):
    calls = []
    monkeypatch.setattr(OUT.requests, "post", fake_api(1, ["May Bejerano"], calls))
    assert OUT.sp_unlock("k", "Wix", "wix.com", "May Bejerano", "Talent Acquisition") == "may@wix.com"
    fetch = [j for name, j in calls if name == "fetch-contacts"]
    assert fetch == [{"filter_id": 7, "limit": 1, "visual_limit": 1}]


def test_pays_nothing_when_several_people_share_the_title(monkeypatch):
    calls = []
    monkeypatch.setattr(OUT.requests, "post", fake_api(4, ["May Bejerano", "Dana Levi"], calls))
    assert OUT.sp_unlock("k", "Wix", "wix.com", "May Bejerano", "Recruiter") is None
    assert not [c for c in calls if c[0] == "fetch-contacts"]
