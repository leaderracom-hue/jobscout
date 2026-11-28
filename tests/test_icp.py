from core import icp


def test_cost_counts_whole_pages():
    assert icp.cost(25) == 0.1 and icp.cost(26) == 0.2
    assert icp.cost(100, "Full + email search") == 1.4


def test_build_input_drops_unknown_codes():
    inp = icp.build_input({"titles": "Founder, CEO", "locations": "Israel", "headcount": ["C", "Z"], "query": "real estate"}, 50)
    assert inp["currentJobTitles"] == ["Founder", "CEO"] and inp["companyHeadcount"] == ["C"]
    assert inp["takePages"] == 2 and inp["maxItems"] == 50 and inp["searchQuery"] == "real estate"
    assert "seniorityLevelIds" not in inp


def test_rows_fallbacks_and_formula_guard():
    r = icp.rows([{"firstName": "Dana", "lastName": "Levi", "headline": "=cmd", "location": {"linkedinText": "Tel Aviv"},
                   "currentPosition": [{"companyName": "Acme"}], "linkedinUrl": "https://www.linkedin.com/in/dana"}, {}])
    assert r[0]["name"] == "Dana Levi" and r[0]["company"] == "Acme" and r[0]["location"] == "Tel Aviv" and r[0]["headline"] == "'=cmd"
    assert r[1]["name"] == "לא נמצא" and list(r[1]) == icp.COLUMNS


def test_sp_body_and_rows():
    b = icp.sp_body({"titles": "CEO, Founder", "locations": "Israel", "headcount": ["C", "D"]}, 900)
    assert b["limit"] == 500 and b["title"] == ["CEO", "Founder"] and b["companyHeadCount"] == ["0 - 25", "100 - 250", "25 - 100"]
    items = [{"id": "a", "fullName": "Dana", "title": "CEO", "industry": "Real Estate", "company": {"name": "X", "website": "x.co"}, "country": "Israel",
              "emailDeliverability": 0.95},
             {"id": "b", "fullName": "Eli", "title": "Intern", "industry": "Real Estate", "company": {}},
             {"id": "c", "fullName": "Gil", "title": "CEO", "industry": "Software", "company": {}}]
    r = icp.sp_rows(items, {"query": "real estate", "exclude_titles": "Intern"})
    assert [x["name"] for x in r] == ["Dana"] and r[0]["website"] == "x.co" and r[0]["sp_id"] == "a" and r[0]["source"] == "SmartProspect"
